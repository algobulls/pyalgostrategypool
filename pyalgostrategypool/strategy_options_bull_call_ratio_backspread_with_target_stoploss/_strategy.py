"""
Strategy Description:
   The Options Bull Call Ratio Backspread strategy establishes a bullish position when a bullish trend confirmation occurs.
   Upon confirmation, it buys an at-the-money (ATM) Call option and sells two higher-strike Call options with the same expiry.
   This variant adds a target profit and a hard stop-loss to manage risk and lock in gains.
   It also allows controlled re-entries when exit conditions are met and bullish conditions reappear.

Strategy Resources:
   - Strategy-specific docs: https://algobulls.github.io/pyalgotrading/strategies/options_bull_call_spread_with_target_stops_and_reentry/
   - General strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
"""

import talib
from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection


class StrategyOptionsBullCallRatioBackspreadWithTargetStoploss(StrategyOptionsBase):
    """
    Bull Call Ratio Backspread strategy with multiple exit mechanisms:
        • Trend Invalidation Check
        • Target Profit
        • Stop-Loss
        • Optional Re-entry (limited by configured re-entry count)
    """

    name = "Strategy Options Bull Call Ratio Backspread With Target Stoploss"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.no_of_otm_strikes_leg_sell = self.strategy_parameters["NUMBER_OF_OTM_STRIKES_SELL_LEG"]
        self.stoploss_percentage = self.strategy_parameters['STOPLOSS_PERCENTAGE']
        self.target_percentage = self.strategy_parameters['TARGET_PERCENTAGE']
        self.re_entry_limit = self.strategy_parameters['RE_ENTRY_LIMIT']
        self.time_period = self.strategy_parameters['TIME_PERIOD']

        # Internal variables and placeholders
        self.child_instrument_main_orders = None  # Tracks Call orders
        self.number_of_allowed_expiry_dates = 1  # Restrict how many expiry dates can be used
        self.transaction_type = None  # Used to label whether the strategy is net credit or net debit during each entry

        self.validate_parameters()

    def validate_parameters(self):
        """ Validates required strategy parameters. """
        check_argument(
            self.strategy_parameters, "extern_function", lambda x: len(x) >= 5,
            err_message=(
                "Need 5 parameters for this strategy: \n"
                "(1) NUMBER_OF_OTM_STRIKES_SELL_LEG \n"
                "(2) TARGET_PERCENTAGE \n"
                "(3) STOPLOSS_PERCENTAGE \n"
                "(4) RE_ENTRY_LIMIT \n"
                "(5) TIME_PERIOD \n"
            )
        )

        # Validate numeric strategy parameters
        for param in (self.re_entry_limit, self.no_of_otm_strikes_leg_sell, self.time_period):
            check_argument(param, "extern_function", is_positive_int, "Value should be a positive integer")

        # Validate percentage strategy parameters
        for param in (self.target_percentage, self.stoploss_percentage):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >0")

    def initialize(self):
        super().initialize()

        # Reset main orders, spread tracking variables, and re-entry counters
        self.child_instrument_main_orders = {}
        self.spread_current = self.spread_entry = self.stoploss_premium = self.target_premium = self.transaction_type = None
        self.re_entry_count = {}

    def check_exit_conditions(self, base_instrument, child_leg_orders_dict):
        """
        Evaluate all exit rules for the Bull Call Ratio Backspread.

        Checks:
        • Trend Invalidation Check – handled separately in exit selection logic.
        • Target Profit – exit if spread rises beyond the profit threshold.
        • Stop-Loss – exit if spread falls beyond the stop-loss threshold.
        """

        # Retrieve current orders and latest traded prices (LTP) for both legs
        ltp_leg_buy = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.BUY].instrument)
        ltp_leg_sell = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.SELL].instrument)

        # Initialize key levels at entry:
        if not self.spread_entry:
            entry_price_leg_buy = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.BUY].entry_price
            entry_price_leg_sell = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.SELL].entry_price
            self.spread_entry = entry_price_leg_buy - 2 * entry_price_leg_sell  # spread at entry
            (multiplier, self.transaction_type) = (1, "Net Debit") if self.spread_entry > 0 else (-1, "Net Credit")
            self.stoploss_premium = self.spread_entry * (1 - multiplier * self.stoploss_percentage / 100)
            self.target_premium = self.spread_entry * (1 + multiplier * self.target_percentage / 100)

        # Current spread price
        self.spread_current = ltp_leg_buy - 2 * ltp_leg_sell

        # Target Profit and Stop-Loss Threshold Check based on spread value
        if self.spread_current > self.target_premium or self.spread_current < self.stoploss_premium:
            (threshold_name, threshold) = ("Target", self.target_premium) if self.spread_current > self.target_premium else ("Stoploss", self.stoploss_premium)
            self.logger.info(
                f"{threshold_name} threshold reached: Transaction Type: {self.transaction_type} | Entry Net Premium: {self.spread_entry:.2f} | Current Net Premium: {self.spread_current:.2f} | {threshold_name} Threshold: {threshold:.2f} - Exiting positions...")
            self.spread_entry = None
            return True

        return False

    def exit_all_positions_for_base_instrument(self, base_instrument):
        for order in filter(None, self.child_instrument_main_orders.get(base_instrument).values()):  # Exit all active positions for the base instrument.
            instrument = order.instrument
            qty = order.quantity
            action = BrokerOrderTransactionTypeConstants.SELL if order.order_transaction_type is BrokerOrderTransactionTypeConstants.BUY else BrokerOrderTransactionTypeConstants.BUY
            position = BrokerExistingOrderPositionConstants.EXIT
            current_ltp = self.broker.get_ltp(instrument)
            _order = self.broker.OrderRegular(instrument=instrument, order_transaction_type=action, price=current_ltp, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.LIMIT, quantity=qty, position=position,
                                              related_order=order)

        # Remove references to the base instrument after exiting CE orders.
        self.child_instrument_main_orders.pop(base_instrument, None)

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            # Skip the instrument if active order already exists
            if self.child_instrument_main_orders.get(instrument):
                continue

            self.logger.debug(
                f"Checking entry conditions for base instrument: {instrument} | "
                f"Determining ATM/OTM option instruments and verifying if CE orders are already placed."
            )

            # Retrieve LTP of the base instrument to setup child instruments
            base_instrument_ltp = self.broker.get_ltp(instrument)

            # Track re-entry count for this instrument
            re_entry_count = self.re_entry_count.get(instrument)

            # If re-entry count exceeds the allowed limit, skip further re-entries
            if re_entry_count is not None and re_entry_count >= self.re_entry_limit:
                self.logger.debug(f"Reentry limit ({self.re_entry_limit}) exceeded. Skipping reentries for {instrument}...")
                continue

            leg_wise_list = [
                (BrokerOrderTransactionTypeConstants.BUY, OptionsStrikeDirection.ATM.value, 0),
                (BrokerOrderTransactionTypeConstants.SELL, OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_leg_sell)
            ]
            hist_data = self.get_historical_data(instrument)
            ema = talib.EMA(hist_data['close'], timeperiod=self.time_period)
            self.logger.debug(f"Latest candle close: {hist_data['close'].iloc[-1]} | Previous candle high: {hist_data['high'].iloc[-2]}")
            if hist_data['close'].iloc[-1] > hist_data['high'].iloc[-2] and self.utils.crossover(hist_data['close'], ema) == 1:
                for action, strike_direction, no_of_strikes in leg_wise_list:
                    self.options_instruments_set_up_all_expiries(instrument, 'CE', base_instrument_ltp)  # Set up option instruments for available expiries
                    child_instrument = self.get_child_instrument_details(instrument, 'CE', strike_direction, no_of_strikes)  # Retrieve ATM/OTM child instrument details for the given instrument

                    # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                    self.instruments_mapper.add_mappings(instrument, child_instrument)

                    selected_instruments.append(child_instrument)
                    meta.append({"action": action, "base_instrument": instrument, "strike_direction": strike_direction})
        # Increment re-entry count for each base instrument entry
        if selected_instruments:
            self.re_entry_count[instrument] = self.re_entry_count[instrument] + 1 if self.re_entry_count.get(instrument) is not None else 0

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):

        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        current_ltp = self.broker.get_ltp(child_instrument)
        multiplier = 1 if meta["strike_direction"] == OptionsStrikeDirection.ATM.value else 2
        qty = self.number_of_lots * child_instrument.lot_size * multiplier
        _order = self.broker.OrderRegular(instrument=child_instrument, order_transaction_type=meta['action'], price=current_ltp, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.LIMIT, quantity=qty)

        # Store details of successful orders
        if check_order_placed_successfully(_order):
            self.child_instrument_main_orders.setdefault(base_instrument, {})[meta['action']] = _order
        else:

            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other leg, if possible and stopping strategy.')
            self.exit_all_positions_for_base_instrument(base_instrument)
            raise ABSystemExit

        return _order

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments_bucket, meta, _base_instruments_processed_list = [], [], []

        for instrument in instruments_bucket:
            if self.instruments_mapper.is_child_instrument(instrument):
                base_instrument = self.instruments_mapper.get_base_instrument(instrument)

                if base_instrument in _base_instruments_processed_list:
                    continue  # Skip if already processed

                _base_instruments_processed_list.append(base_instrument)

                # Check if both leg orders are complete and evaluate trend invalidation or spread exit conditions.
                child_leg_orders_dict = self.child_instrument_main_orders.get(base_instrument)
                if child_leg_orders_dict:
                    hist_data = self.get_historical_data(instrument)
                    ema = talib.EMA(hist_data['close'], timeperiod=self.time_period)
                    bearish_trend_check = hist_data['close'].iloc[-1] < hist_data['high'].iloc[-2] and self.utils.crossover(hist_data['close'], ema) == -1
                    if all(check_order_complete_status(order) for order in child_leg_orders_dict.values()) and (bearish_trend_check or self.check_exit_conditions(base_instrument, child_leg_orders_dict)):
                        selected_instruments_bucket.extend(order.instrument for order in child_leg_orders_dict.values() if order)
                        meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(self.child_instrument_main_orders))

        return selected_instruments_bucket, meta

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

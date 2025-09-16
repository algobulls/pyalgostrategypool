"""
Strategy Description:
    The Options Bull Call Spread strategy establishes a bullish position by buying an at-the-money (ATM) Call option and selling a higher-strike Call option with the same expiry.
    This variant adds a target profit, a hard stop-loss, and a trailing stop-loss to manage risk and lock in gains.
    It also allows controlled re-entries when the exit conditions are met and market conditions justify a new entry.

Strategy Resources:
    - Strategy-specific docs: https://algobulls.github.io/pyalgotrading/strategies/options_bull_call_spread_with_target_stops_and_reentry/
    - General strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
"""

from pyalgotrading.constants import ABSystemExit
from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection
from pyalgotrading.strategy.utils import check_order_placed_successfully, check_order_complete_status


class StrategyOptionsBullCallSpreadWithTargetStopsAndReentry(StrategyOptionsBase):
    """
    Bull Call Spread strategy with multiple exit mechanisms:
        • Target Profit
        • Hard Stop-Loss
        • Trailing Stop-Loss
        • Optional Re-entry
    """

    name = "Strategy Options Bull Call Spread With Target Stops And Re-entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.no_of_otm_strikes_leg_sell = self.strategy_parameters["NUMBER_OF_OTM_STRIKES_SELL_LEG"]
        self.stoploss_percentage = self.strategy_parameters['STOPLOSS_PERCENTAGE']
        self.target_percentage = self.strategy_parameters['TARGET_PERCENTAGE']
        self.tsl_percentage = self.strategy_parameters["TRAILING_STOPLOSS_PERCENTAGE"]
        self.re_entry_limit = self.strategy_parameters['RE_ENTRY_LIMIT']

        # Internal variables and placeholders
        self.child_instrument_main_orders = None  # Tracks Call orders
        self.number_of_allowed_expiry_dates = 1  # Restrict how many expiry dates can be used

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
                "(4) TRAILING_STOPLOSS_PERCENTAGE \n"
                "(5) RE_ENTRY_LIMIT"
            )
        )

        # Validate expiry dates
        if len(self.get_allowed_expiry_dates()) != self.number_of_allowed_expiry_dates:
            self.logger.info(f"Allowed expiry dates: {self.number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting...")
            raise ABSystemExit

        # Validate numeric strategy parameters
        for param in (self.re_entry_limit, self.no_of_otm_strikes_leg_sell):
            check_argument(param, "extern_function", is_positive_int, "Value should be a positive integer")

        # Validate percentage strategy parameters
        for param in (self.target_percentage, self.stoploss_percentage, self.tsl_percentage,):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >0")

    def initialize(self):
        super().initialize()

        # Reset main orders, trailing stops and re-entry counts
        self.child_instrument_main_orders = {}
        self.spread_current = self.spread_entry = self.highest = self.trailing_stop = self.stoploss_premium = self.target_premium = None
        self.re_entry_count = {}

    def check_exit_conditions(self, base_instrument, child_leg_orders_dict):
        """
        Evaluate all exit rules for the Bull Call Spread.

        Checks:
        • Target profit – exit if spread rises to the profit target.
        • Hard stop-loss – exit if spread falls below the stop-loss threshold.
        • Trailing stop-loss – once the spread makes new highs, trail a stop to lock in profits.
        """

        # Retrieve current orders and latest traded prices (LTP) for both legs
        ltp_leg_buy = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.BUY].instrument)
        ltp_leg_sell = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.SELL].instrument)

        # Initialize key levels at entry:
        if not self.spread_entry:
            entry_price_leg_buy = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.BUY].entry_price
            entry_price_leg_sell = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.SELL].entry_price
            self.spread_entry = entry_price_leg_buy - entry_price_leg_sell  # spread at entry
            self.stoploss_premium = self.spread_entry * (1 - self.stoploss_percentage / 100)
            self.target_premium = self.spread_entry * (1 + self.target_percentage / 100)

        # Current spread price
        self.spread_current = ltp_leg_buy - ltp_leg_sell

        self.logger.info(f"Target and Hard Stoploss Check: Entry Spread price: {self.spread_entry:.2f}"
                         f"Current Spread price: {self.spread_current:.2f}"
                         f"Target Threshold: {self.target_premium:.2f}"
                         f"Stoploss Threshold : {self.stoploss_premium:.2f}")

        # Target Profit Check
        if self.spread_current > self.target_premium:
            self.logger.debug(f"Target profit reached: Current Net Premium ({self.spread_current}) dropped below Target Threshold ({self.target_premium}). Exiting positions.")
            self.spread_entry = None
            return True

        # Hard Stoploss Check
        if self.spread_current < self.stoploss_premium:
            self.logger.debug(f"Stop-loss triggered: Current Net Premium ({self.spread_current}) exceeded Stop-loss Threshold ({self.stoploss_premium}). Exiting positions.")
            self.spread_entry = None
            return True

        # Activate trailing stop only after spread moves by at least trailing % above entry.
        if not self.highest and self.spread_current > self.spread_entry / (1 - self.tsl_percentage / 100):
            self.highest = self.spread_current  # first highest spread
            self.trailing_stop = self.highest * (1 - self.tsl_percentage / 100)  # initial trailing stop

        # Trailing Stop-loss (TSL) check
        if self.highest:
            self.logger.info(f"Trailing Stoploss Check: Entry Spread price: {self.spread_entry:.2f} "
                             f"Current Spread price: {self.spread_current:.2f}"
                             f"New Highest: {self.highest:.2f}"
                             f"Trailing Stop: {self.trailing_stop:.2f}"
                             f"(Trail %={self.tsl_percentage})")

            # Update trailing stop whenever current spread exceeds previous high
            if self.spread_current > self.highest:
                self.highest = self.spread_current
                self.trailing_stop = self.highest * (1 - self.tsl_percentage / 100)

            # Trigger TSL exit if current spread falls below traiing stop
            if self.spread_current < self.trailing_stop:
                self.logger.info(f"Trailing Stop-loss triggered: Current Net Premium ({self.spread_current} dropped below Trailing Stop ({self.trailing_stop}. Exiting positions.")

                # Reset so next entry can be reinitialize
                self.highest = self.spread_entry = self.trailing_stop = None
                return True

        return False

    def exit_all_positions_for_base_instrument(self, base_instrument):

        for order in filter(None, self.child_instrument_main_orders.get(base_instrument).values()):  # Exit all active positions for the base instrument.
            order.exit_position()

        # Remove references to the base instrument after exiting CE orders.
        self.child_instrument_main_orders.pop(base_instrument, None)

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            self.logger.debug(
                f"Checking entry conditions for base instrument: {instrument} | "
                f"Determining ATM/OTM option instruments and verifying if CE orders are already placed."
            )

            # Skip the instrument if active order already exists
            if self.child_instrument_main_orders.get(instrument):
                continue

            # Retrieve LTP of the base instrument to setup child instruments
            base_instrument_ltp = self.broker.get_ltp(instrument)

            # Track re-entry count for this instrument
            re_entry_count = self.re_entry_count.get(instrument, 0)

            # If re-entry count exceeds the allowed limit, skip further re-entries
            if re_entry_count >= self.re_entry_limit:
                continue

            # otherwise increment re-entry count
            else:
                re_entry_count += 1

            leg_wise_list = [
                (BrokerOrderTransactionTypeConstants.BUY, OptionsStrikeDirection.ATM.value, 0),
                (BrokerOrderTransactionTypeConstants.SELL, OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_leg_sell)
            ]

            for action, strike_direction, no_of_strikes in leg_wise_list:
                self.options_instruments_set_up_all_expiries(instrument, 'CE', base_instrument_ltp)  # Set up option instruments for available expiries
                child_instrument = self.get_child_instrument_details(instrument, 'CE', strike_direction, no_of_strikes)  # Retrieve ATM child instrument details for the given instrument

                # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                self.instruments_mapper.add_mappings(instrument, child_instrument)

                selected_instruments.append(child_instrument)
                meta.append({"action": action, "base_instrument": instrument})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):

        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        _order = self.broker.OrderRegular(instrument=child_instrument, order_transaction_type=meta['action'], order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)

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

                # Check if CE orders are complete and if trailing stop-loss condition is met.
                child_leg_orders_dict = self.child_instrument_main_orders.get(base_instrument)
                if child_leg_orders_dict:
                    if all(check_order_complete_status(order) for order in child_leg_orders_dict.values()) and (self.check_exit_conditions(base_instrument, child_leg_orders_dict)):
                        selected_instruments_bucket.extend(order.instrument for order in child_leg_orders_dict.values() if order)
                        meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(self.child_instrument_main_orders))

        return selected_instruments_bucket, meta

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True
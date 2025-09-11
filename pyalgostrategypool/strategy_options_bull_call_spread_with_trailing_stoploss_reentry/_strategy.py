"""
Strategy Description:
    The Options Bull Call Spread strategy establishes a bullish position by buying an at-the-money (ATM) Call option and selling a higher-strike Call option with the same expiry.
    This variant adds a trailing stop-loss to protect profits.
    It also allows controlled re-entries when the trailing stop is triggered and market conditions justify a new entry.

Strategy Resources:
    - Strategy-specific docs: https://algobulls.github.io/pyalgotrading/strategies/options_bull_call_spread_with_trailing_stoploss_reentry/
    - General strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
"""

from constants import BrokerOrderTransactionTypeConstants, BrokerOrderVarietyConstants
from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection
from strategy.utils import check_order_placed_successfully, check_order_complete_status
from utils.ab_system_exit import ABSystemExit
from utils.func import check_argument, is_nonnegative_int_or_float


class StartegyOptionsBullCallSpreadWithTrailingStoplossRentry(StrategyOptionsBase):
    """ Bull Call Spread Strategy with Trailing Stop-loss and Re-entry. """

    name = "Strategy Options Bull Call Spread With Trailing Stop-loss & Re-entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.no_of_otm_strikes_leg_sell = self.strategy_parameters["NUMBER_OF_OTM_STRIKES_SELL_LEG"]
        self.tsl_percentage = self.strategy_parameters["TRAILING_STOPLOSS_PERCENTAGE"]
        self.re_entry_limit = self.strategy_parameters['RE_ENTRY_LIMIT']

        # Internal variables and placeholders
        self.child_instrument_main_orders = None  # Tracks Call orders
        self.number_of_allowed_expiry_dates = 1  # Restrict how many expiry dates can be used
        self.validate_parameters()

    def validate_parameters(self):
        """ Validates required strategy parameters. """
        check_argument(self.strategy_parameters, "extern_function", lambda x: len(x) >= 2, err_message="Need 2 parameters for this strategy: \n(1) TRAILING STOPLOSS PERCENTAGE \n(2) REENTRY COUNT")

        # Validate expiry dates
        if len(self.get_allowed_expiry_dates()) != self.number_of_allowed_expiry_dates:
            self.logger.info(f"Allowed expiry dates: {self.number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting...")
            raise SystemExit

        # Validate parameters
        for param in (self.re_entry_limit, self.tsl_percentage):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >0")

    def initialize(self):
        super().initialize()

        # Reset main orders, trailing stops and re-entry counts
        self.child_instrument_main_orders = {}
        self.spread_current = self.spread_entry = self.highest = self.stop = None
        self.re_entry_count = {}

    def trailing_stop_spread(self, base_instrument, trail_percentage=20):
        """
        Manage a trailing stop-loss for Bull Call Spread (BUY leg price- SELL leg price).
        """

        ltp_leg_buy = self.broker.get_ltp(self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.BUY].instrument)
        ltp_leg_sell = self.broker.get_ltp(self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.SELL].instrument)

        # Define initial spread and trailing stop
        if not self.spread_entry:
            entry_price_leg_buy = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.BUY].entry_price
            entry_price_leg_sell = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.SELL].entry_price
            self.spread_entry = entry_price_leg_buy - entry_price_leg_sell  # spread at entry
            self.highest = self.spread_entry  # set highest spread so far
            self.stop = self.highest * (1 - trail_percentage / 100)  # trailing stop

        # Current spread price
        self.spread_current = ltp_leg_buy - ltp_leg_sell

        self.logger.info(f"TSL check triggered. Monitoring stop-loss..")
        self.logger.info(
            f"TSL update: new_highest={self.highest:.2f} "
            f"stop={self.stop:.2f} (trail%={trail_percentage})"
        )

        # Update trailing stop whenever current spread exceeds previous high
        if self.spread_current > self.highest:
            self.highest = self.spread_current
            self.stop = self.highest * (1 - trail_percentage / 100)

        # Trigger exit if current spread falls below traiing stop
        if self.spread_current < self.stop:
            self.logger.info(f'Entry Spread price: {self.spread_entry:.2f} | Current Spread price:{self.spread_current:.2f} | Trailing stop:{self.stop:.2f}'
                             f'Trailing stop loss hit. Exiting order')

            # Reset so next entry can be reinitialize
            self.highest = self.spread_entry = self.stop = None
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
        if meta['action'] == BrokerOrderTransactionTypeConstants.BUY:
            _order = self.broker.BuyOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)
        else:
            _order = self.broker.SellOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)

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
                if self.child_instrument_main_orders.get(base_instrument):
                    if all(check_order_complete_status(order) for order in list(self.child_instrument_main_orders.get(base_instrument).values())) and (self.trailing_stop_spread(base_instrument, self.tsl_percentage)):
                        selected_instruments_bucket.extend(order.instrument for order in list(self.child_instrument_main_orders.get(base_instrument).values()) if order)
                        meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(self.child_instrument_main_orders))

        return selected_instruments_bucket, meta

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

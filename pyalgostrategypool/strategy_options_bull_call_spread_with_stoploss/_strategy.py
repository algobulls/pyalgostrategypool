from constants import *
from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection
# try:
#     from strategy.utils import check_order_placed_sucessfully
# except Exception as e:
#     print(e)
from utils.ab_system_exit import ABSystemExit
from utils.func import check_argument, is_nonnegative_int_or_float


class StartegyOptionsBullCallSpreadWithStoplossTarget(StrategyOptionsBase):
    """ Bull Call Spread Strategy with Trailing Stop-loss and Re-entry. """

    name = "Strategy Options Bull Call Spread With Stop-loss & Re-entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.no_of_otm_strikes_sell_leg = self.strategy_parameters["NUMBER_OF_OTM_STRIKES_SELL_CALL_LEG"]
        self.tsl_percentage = self.strategy_parameters["TRAILING_STOPLOSS_PERCENTAGE"]
        self.re_entry_limit = self.strategy_parameters['RE_ENTRY_COUNT']

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

            # Track re-entry count for this instrument
            re_entry_count = self.re_entry_count.get(instrument)
            if re_entry_count is not None:
                # If re-entry count exceeds the allowed limit, skip further re-entries
                if self.re_entry_count[instrument] >= self.re_entry_limit:
                    continue
                else:
                    # Otherwise increment re-entry count
                    self.re_entry_count[instrument] += 1
            else:
                # Initialize the count for first time instruments
                self.re_entry_count[instrument] = 0

            leg_wise_list = [
                (ActionConstants.ENTRY_BUY, OptionsStrikeDirection.ATM.value, 0),
                (ActionConstants.ENTRY_SELL, OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_sell_leg)
            ]

            for action, strike_direction, no_of_strikes in leg_wise_list:
                self.options_instruments_set_up_all_expiries(instrument, 'CE', self.broker.get_ltp(instrument))  # Set up option instruments for available expiries
                child_instrument = self.get_child_instrument_details(instrument, 'CE', strike_direction, no_of_strikes)  # Retrieve ATM child instrument details for the given instrument

                # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                self.instruments_mapper.add_mappings(instrument, child_instrument)

                selected_instruments.append(child_instrument)
                meta.append({"action": action, "base_instrument": instrument})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):

        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        if meta['action'] == ActionConstants.ENTRY_BUY:
            _order = self.broker.BuyOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)
        else:
            _order = self.broker.SellOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)

        if self.check_order_placed_successfully(_order):
            if meta['action'] == ActionConstants.ENTRY_BUY:
                self.child_instrument_main_orders.setdefault(base_instrument, {})["BUY"] = _order
            else:
                self.child_instrument_main_orders.setdefault(base_instrument, {})["SELL"] = _order
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
                    if all(self.check_order_complete_status(order) for order in list(self.child_instrument_main_orders.get(base_instrument).values())) and (self.trailing_stop_spread(base_instrument, self.tsl_percentage)):
                        selected_instruments_bucket.extend(order.instrument for order in list(self.child_instrument_main_orders.get(base_instrument).values()) if order)
                        meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(self.child_instrument_main_orders))

        return selected_instruments_bucket, meta

    def exit_all_positions_for_base_instrument(self, base_instrument):

        for order in filter(None, self.child_instrument_main_orders.get(base_instrument).values()):  # Exit all active positions for the base instrument.
            order.exit_position()

        # Remove references to the base instrument after exiting CE orders.
        self.child_instrument_main_orders.pop(base_instrument, None)

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

    def trailing_stop_spread(self, base_instrument, trail_percentage=20):
        """
        Manage a trailing stop-loss for Bull Call Spread (BUY leg price- SELL leg price).
        """

        ltp_leg_buy = self.broker.get_ltp(self.child_instrument_main_orders.get(base_instrument)["BUY"].instrument)
        ltp_leg_sell = self.broker.get_ltp(self.child_instrument_main_orders.get(base_instrument)["SELL"].instrument)

        # Define initial spread and trailing stop
        if not self.spread_entry:
            entry_price_leg_buy = self.child_instrument_main_orders.get(base_instrument)["BUY"].entry_price
            entry_price_leg_sell = self.child_instrument_main_orders.get(base_instrument)["SELL"].entry_price
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

        # update trailing stop
        if self.spread_current > self.highest:
            self.highest = self.spread_current
            self.stop = self.highest * (1 - trail_percentage / 100)

        if self.spread_current < self.stop:
            self.logger.info(f'Entry Spread price: {self.spread_entry:.2f} | Current Spread price:{self.spread_current:.2f} | Trailing stop:{self.stop:.2f}'
                             f'Trailing stop loss hit. Exiting order')

            # Reset so next entry can be reinitialize
            self.highest = self.spread_entry = self.stop = None
            return True

        return False

    # TODO: Below functions will be removed once import error gets fixed
    def check_order_placed_successfully(self, _order):
        """
            This method checks whether --
            -- order is not None
            -- broker_order_id exists for this order
            -- order status is not REJECTED
            Returns True if all of the above are True, else False.
        """
        return _order is not None and _order.broker_order_id is not None and _order.get_order_status() != BrokerOrderStatusConstants.REJECTED

    def check_order_complete_status(self, _order):
        """
        This method checks whether --
        -- order is not None
        -- broker_order_id exists for this order
        -- order status is COMPLETE

        Returns True if all of the above are True, else False
        """
        return _order is not None and _order.broker_order_id is not None and _order.get_order_status() == BrokerOrderStatusConstants.COMPLETE

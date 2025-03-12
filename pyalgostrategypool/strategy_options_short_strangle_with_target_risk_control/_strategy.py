from constants import *
from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection
from utils.ab_system_exit import ABSystemExit
from utils.func import check_argument, is_nonnegative_int_or_float, is_positive_int


class StrategyOptionsShortStrangleWithTargetRiskControl(StrategyOptionsBase):
    """ Short Strangle Strategy that exits based on either a target percentage or stop-loss. """

    name = "Strategy Options Short Strangle With Target & Risk Control"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.number_of_strikes = self.strategy_parameters['NUMBER_OF_STRIKES']
        self.target_percentage = self.strategy_parameters["TARGET_PERCENTAGE"]
        self.stoploss_multiplier = self.strategy_parameters["STOPLOSS_MULTIPLIER"]


        # Internal variables and placeholders
        self.child_instrument_main_order_ce = self.child_instrument_main_order_pe = None  # Tracks Call & Put orders
        self.number_of_allowed_expiry_dates = 1  # Restrict how many expiry dates can be used

        self.validate_parameters()

    def validate_parameters(self):
        """ Validates required strategy parameters. """
        check_argument(self.strategy_parameters, "extern_function", lambda x: len(x) >= 3, err_message="Need 3 parameters for this strategy: \n(1) NUMBER_OF_STRIKES \n(2) TARGET_PERCENTAGE \n(3) STOPLOSS_MULTIPLIER")

        # Validate number of strikes
        check_argument(self.number_of_strikes, "extern_function", is_positive_int, "Value should be positive integer")

        # Validate expiry dates
        if len(self.get_allowed_expiry_dates()) != self.number_of_allowed_expiry_dates:
            self.logger.info(f"Allowed expiry dates: {self.number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting...")
            raise SystemExit

        # Validate parameters
        for param in (self.target_percentage, self.stoploss_multiplier):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >0.0")

    def initialize(self):
        super().initialize()

        # Reset main orders for calls and puts
        self.child_instrument_main_order_ce, self.child_instrument_main_order_pe = {}, {}

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            self.logger.debug(
                f"Checking entry conditions for base instrument: {instrument} | "
                f"Determining OTM option instruments and verifying if CE/PE orders are already placed."
            )
            leg_wise_list = [
                ("CE", self.child_instrument_main_order_ce.get(instrument)),
                ("PE", self.child_instrument_main_order_pe.get(instrument))
            ]

            for tradingsymbol_suffix, main_order in leg_wise_list:
                if main_order:
                    continue  # Skip processing if an order has already been placed for this leg

                self.options_instruments_set_up_all_expiries(instrument, tradingsymbol_suffix, self.broker.get_ltp(instrument))  # Set up option instruments for available expiries
                child_instrument = self.get_child_instrument_details(instrument, tradingsymbol_suffix, OptionsStrikeDirection.OTM.value, self.number_of_strikes)  # Retrieve OTM child instrument details for the given instrument

                # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                self.instruments_mapper.add_mappings(instrument, child_instrument)

                selected_instruments.append(child_instrument)
                meta.append({"action": ActionConstants.ENTRY_SELL, "base_instrument": instrument, "tradingsymbol_suffix": tradingsymbol_suffix})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):

        child_instrument = instrument
        _order = self.broker.SellOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)

        if self.check_order_placed_successfully(_order):
            (self.child_instrument_main_order_ce if meta["tradingsymbol_suffix"] == "CE" else self.child_instrument_main_order_pe)[meta["base_instrument"]] = _order
        else:
            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other leg, if possible and stopping strategy.')
            base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
            self.exit_all_positions_for_base_instrument(base_instrument)
            raise ABSystemExit

        return _order

    def check_exit_condition(self, main_order_ce, main_order_pe):
        """ Determines if the strategy should exit based on stoploss or target. Returns True if exit condition is met. """
        ltp_ce = self.broker.get_ltp(main_order_ce.instrument)
        ltp_pe = self.broker.get_ltp(main_order_pe.instrument)
        total_ltp = ltp_ce + ltp_pe

        net_premium_cost = main_order_ce.entry_price + main_order_pe.entry_price
        stop_loss_premium = net_premium_cost * self.stoploss_multiplier
        target_premium = net_premium_cost * (1 - self.target_percentage / 100)

        self.logger.debug(f"LTP (CE) : {ltp_ce}, LTP (PE) : {ltp_pe}, "
                          f"Net Entry Premium : {net_premium_cost} | Current Net Premium : {total_ltp} "
                          f"Stop-loss Threshold : {stop_loss_premium} | Target Threshold : {target_premium}")

        target_profit_condition = total_ltp < target_premium
        if target_profit_condition:
            self.logger.debug(f"Target profit reached: Current Net Premium ({total_ltp}) dropped below Target Threshold ({target_premium}). Exiting position.")
            return True

        stop_loss_condition = total_ltp > stop_loss_premium
        if stop_loss_condition:
            self.logger.debug(f"Stop-loss triggered: Current Net Premium ({total_ltp}) exceeded Stop-loss Threshold ({stop_loss_premium}). Exiting position.")
            return True

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments_bucket, meta, _base_instruments_processed_list = [], [], []

        for instrument in instruments_bucket:
            if self.instruments_mapper.is_child_instrument(instrument):
                base_instrument = self.instruments_mapper.get_base_instrument(instrument)

                if base_instrument in _base_instruments_processed_list:
                    continue  # Skip if already processed

                _base_instruments_processed_list.append(base_instrument)
                main_orders = [self.child_instrument_main_order_ce.get(base_instrument), self.child_instrument_main_order_pe.get(base_instrument)]

                # Check if both CE and PE orders are complete and if exit conditions are met.
                if all(self.check_order_complete_status(order) for order in main_orders) and self.check_exit_condition(*main_orders):
                    selected_instruments_bucket.extend(order.instrument for order in main_orders if order)
                    meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(main_orders))

        return selected_instruments_bucket, meta

    def exit_all_positions_for_base_instrument(self, base_instrument):
        child_instrument_main_orders = [self.child_instrument_main_order_ce.get(base_instrument), self.child_instrument_main_order_pe.get(base_instrument)]

        for order in filter(None, child_instrument_main_orders):  # Exit all active positions for the base instrument.
            order.exit_position()

        # Remove references to the base instrument after exiting both CE and PE orders.
        self.child_instrument_main_order_ce.pop(base_instrument, None)
        self.child_instrument_main_order_pe.pop(base_instrument, None)

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

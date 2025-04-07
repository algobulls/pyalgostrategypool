from constants import *
from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection
from utils.ab_system_exit import ABSystemExit
from utils.func import check_argument, is_nonnegative_int_or_float, is_positive_int


class StrategyOptionsLongIronCondorReentry(StrategyOptionsBase):
    """ Long Iron Condor Strategy that exits and re-enters based on specific conditions  """

    name = "Strategy Options Long Iron Condor With Re-Entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.no_of_otm_strikes_buy_ce_leg = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_BUY_CALL_LEG", 1)
        self.no_of_otm_strikes_sell_ce_leg = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_SELL_CALL_LEG", 3)
        self.no_of_otm_strikes_buy_pe_leg = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_BUY_PUT_LEG", 1)
        self.no_of_otm_strikes_sell_pe_leg = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_SELL_PUT_LEG", 3)
        self.re_entry_upper_level = self.strategy_parameters.get("RE_ENTRY_UPPER_LEVEL", 100) if self.strategy_parameters.get("ALLOW_REENTRY_UPPER", 0) == 1 else None
        self.re_entry_lower_level = self.strategy_parameters.get("RE_ENTRY_LOWER_LEVEL", 100) if self.strategy_parameters.get("ALLOW_REENTRY_LOWER", 0) == 1 else None

        # Internal variables and placeholders
        self.child_instrument_main_orders_ce = self.child_instrument_main_orders_pe = None  # Tracks Call & Put orders
        self.lower_level_re_entry_condition = self.upper_level_re_entry_condition = None
        self.reference_price = None
        self.number_of_allowed_expiry_dates = 1  # Restrict how many expiry dates can be used

        self.validate_parameters()

    def validate_parameters(self):

        # Validate expiry dates
        if len(self.get_allowed_expiry_dates()) != self.number_of_allowed_expiry_dates:
            self.logger.info(f"Allowed expiry dates: {self.number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting...")
            raise SystemExit

        # Validate parameters
        for param in (self.re_entry_upper_level, self.re_entry_lower_level):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >=0.0")

        for param in (self.no_of_otm_strikes_buy_ce_leg, self.no_of_otm_strikes_sell_ce_leg, self.no_of_otm_strikes_buy_pe_leg, self.no_of_otm_strikes_sell_pe_leg):
            check_argument(param, 'extern_function', is_positive_int, 'Number of strikes should be an integer > 0')

        for param in [self.strategy_parameters.get("ALLOW_REENTRY_UPPER", 0), self.strategy_parameters.get("ALLOW_REENTRY_LOWER", 0)]:
            check_argument(param, 'extern_function', lambda x: isinstance(x, int) and x in [0, 1], f'ALLOW_REENTRY parameters should be 0 (False) or 1 (True)')

    def initialize(self):
        super().initialize()

        # Reset main orders for calls and puts
        self.child_instrument_main_orders_ce, self.child_instrument_main_orders_pe = {}, {}

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            self.logger.debug(
                f"Checking entry/re-entry conditions for base instrument: {instrument} | "
                f"Determining OTM option instruments and verifying if CE/PE orders are already placed."
            )
            # Define a list of tuples for managing legs, their types, and relevant orders
            leg_wise_list = [
                ("ce_buy_leg", 'CE', self.no_of_otm_strikes_buy_ce_leg, 'BUY', self.child_instrument_main_orders_ce[instrument].get("ce_buy_leg") if self.child_instrument_main_orders_ce.get(instrument) else None),
                ("ce_sell_leg", 'CE', self.no_of_otm_strikes_sell_ce_leg, 'SELL', self.child_instrument_main_orders_ce[instrument].get("ce_sell_leg") if self.child_instrument_main_orders_ce.get(instrument) else None),
                ("pe_buy_leg", 'PE', self.no_of_otm_strikes_buy_pe_leg, 'BUY', self.child_instrument_main_orders_pe[instrument].get("pe_buy_leg") if self.child_instrument_main_orders_pe.get(instrument) else None),
                ("pe_sell_leg", 'PE', self.no_of_otm_strikes_sell_pe_leg, 'SELL', self.child_instrument_main_orders_pe[instrument].get("pe_sell_leg") if self.child_instrument_main_orders_pe.get(instrument) else None)
            ]

            current_underlying_price = self.broker.get_ltp(instrument)

            # Calculate re-entry conditions based on the latest price
            upper_reverse_re_entry = (self.upper_level_re_entry_condition and (self.reference_price - current_underlying_price) >= self.re_entry_upper_level)
            lower_reverse_re_entry = (self.lower_level_re_entry_condition and (-self.reference_price + current_underlying_price) >= self.re_entry_lower_level)

            # Check if there are no active orders (PE/CE legs), no re-entry conditions to be applied
            no_open_orders = (not self.child_instrument_main_orders_pe and not self.child_instrument_main_orders_ce)
            no_re_entry_conditions = (not self.upper_level_re_entry_condition and not self.lower_level_re_entry_condition)

            # Proceed only if no open orders or the re-entry conditions are met
            if no_open_orders and (upper_reverse_re_entry or lower_reverse_re_entry or no_re_entry_conditions):
                for leg, tradingsymbol_suffix, no_of_strikes, action, main_order in leg_wise_list:
                    self.options_instruments_set_up_all_expiries(instrument, tradingsymbol_suffix, self.broker.get_ltp(instrument))  # Set up option instruments for available expiries
                    child_instrument = self.get_child_instrument_details(instrument, tradingsymbol_suffix, OptionsStrikeDirection.OTM.value, no_of_strikes)  # Retrieve OTM child instrument details for the given instrument
                    self.logger.debug(f'child instrument details:{child_instrument}')
                    # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                    self.instruments_mapper.add_mappings(instrument, child_instrument)

                    selected_instruments.append(child_instrument)
                    self.logger.debug(f'selected instruments list:{selected_instruments}')

                    meta.append({"leg": leg, "action": action, "base_instrument": instrument, "tradingsymbol_suffix": tradingsymbol_suffix})

            if not no_re_entry_conditions: self.lower_level_re_entry_condition = self.upper_level_re_entry_condition = None
        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):

        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        self.reference_price = self.broker.get_ltp(base_instrument)
        if meta['action'] == 'BUY':
            _order = self.broker.BuyOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)
        else:
            _order = self.broker.SellOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)

        if self.check_order_placed_successfully(_order):
            (self.child_instrument_main_orders_ce.setdefault(meta['base_instrument'], {}) if meta["tradingsymbol_suffix"] == "CE" else self.child_instrument_main_orders_pe.setdefault(meta['base_instrument'], {}))[meta['leg']] = _order
        else:
            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other legs, if possible and stopping strategy.')
            self.exit_all_positions_for_base_instrument(base_instrument)
            raise ABSystemExit

        return _order

    def check_exit_condition(self, base_instrument):
        """ Determines if the strategy should exit based on re-rentry conditions. Returns True if exit condition is met. """

        current_underlying_price = self.broker.get_ltp(base_instrument)
        self.upper_level_re_entry_condition = current_underlying_price - self.reference_price >= self.re_entry_upper_level if self.re_entry_upper_level else None
        self.lower_level_re_entry_condition = self.reference_price - current_underlying_price >= self.re_entry_lower_level if self.re_entry_lower_level else None

        if self.upper_level_re_entry_condition or self.lower_level_re_entry_condition:
            self.logger.debug(f'Re-entry thresholds breached. Exiting current positions for all legs')
            self.reference_price = current_underlying_price
            return True

        return False

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments_bucket, meta, _base_instruments_processed_list = [], [], []

        for instrument in instruments_bucket:
            if self.instruments_mapper.is_child_instrument(instrument):
                base_instrument = self.instruments_mapper.get_base_instrument(instrument)

                if base_instrument in _base_instruments_processed_list:
                    continue  # Skip if already processed

                _base_instruments_processed_list.append(base_instrument)
                main_orders = [*self.child_instrument_main_orders_ce.get(base_instrument).values(), *self.child_instrument_main_orders_pe.get(base_instrument).values()]

                # Checks if all CE and PE orders are complete and if exit conditions are met.
                if all(self.check_order_complete_status(order) for order in main_orders) and self.check_exit_condition(base_instrument):
                    selected_instruments_bucket.extend(order.instrument for order in main_orders if order)
                    meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(main_orders))

        return selected_instruments_bucket, meta

    def exit_all_positions_for_base_instrument(self, base_instrument):
        child_instrument_main_orders = [*self.child_instrument_main_orders_ce.get(base_instrument, {}).values(), *self.child_instrument_main_orders_pe.get(base_instrument, {}).values()] or [None]

        for order in filter(None, child_instrument_main_orders):  # Exit all active positions for the base instrument.
            order.exit_position()

        # Remove references to the base instrument after exiting both CE and PE orders.
        self.child_instrument_main_orders_ce.pop(base_instrument, None)
        self.child_instrument_main_orders_pe.pop(base_instrument, None)

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

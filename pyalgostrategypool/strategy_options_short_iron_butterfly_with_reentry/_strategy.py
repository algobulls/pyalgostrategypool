"""
   Strategy Description:
       The Options Short Iron Butterfly strategy is a defined-risk, four-leg options setup centered around an at-the-money strike.
       It involves selling both a Call and a Put at the same (ATM) strike price, while simultaneously buying a further out-of-the-money Call and Put.
       This strategy is typically used in low volatility conditions when minimal price movement is expected, and the goal is to profit from time decay.
       Maximum profit is achieved if the underlying price remains near the short strike at expiration, while losses are capped if the price moves beyond the long wings.
"""

from constants import *
from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection
from strategy.utils import check_order_placed_successfully, check_order_complete_status
from utils.ab_system_exit import ABSystemExit
from utils.func import check_argument, is_nonnegative_int_or_float, is_positive_int


class StrategyOptionsShortIronButterflyReentry(StrategyOptionsBase):
    """ Short Iron Butterfly Strategy that exits and re-enters based on price-level breaches. """

    name = "Strategy Options Short Iron Butterfly With Re-Entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Number of strikes away from ATM for the long wings (OTM buys)
        self.no_of_otm_strikes_buy_ce_leg = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_BUY_CALL_LEG", 2)
        self.no_of_otm_strikes_buy_pe_leg = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_BUY_PUT_LEG", 2)

        # Price offsets used to detect breach and trigger exit; enabled via flags
        self.price_breach_upper_offset = self.strategy_parameters.get("PRICE_BREACH_UPPER_OFFSET", 100) if self.strategy_parameters.get("ALLOW_UPPER_PRICE_BREACH", 0) == 1 else None
        self.price_breach_lower_offset = self.strategy_parameters.get("PRICE_BREACH_LOWER_OFFSET", 100) if self.strategy_parameters.get("ALLOW_LOWER_PRICE_BREACH", 0) == 1 else None

        # Internal variables and placeholders
        self.child_instrument_main_orders_ce = self.child_instrument_main_orders_pe = None  # Tracks Call & Put orders
        self.flag_lower_breach_possible_reentry = self.flag_upper_breach_possible_reentry = False
        self.base_price_post_breach = None
        self.number_of_allowed_expiry_dates = 1  # Restrict how many expiry dates can be used

        self.validate_parameters()

    def validate_parameters(self):

        # Validate expiry dates
        if len(self.get_allowed_expiry_dates()) != self.number_of_allowed_expiry_dates:
            self.logger.info(f"Allowed expiry dates: {self.number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting...")
            raise ABSystemExit

        # Validate parameters
        for param in (self.price_breach_upper_offset, self.price_breach_lower_offset):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "PRICE_BREACH_*_OFFSET should be a non-negative number (>= 0.0)")

        for param in (self.no_of_otm_strikes_buy_ce_leg, self.no_of_otm_strikes_buy_pe_leg):
            check_argument(param, "extern_function", is_positive_int, "NUMBER_OF_OTM_STRIKES_* parameters should be positive integers (> 0)")

        for param in [self.strategy_parameters.get("ALLOW_UPPER_PRICE_BREACH", 0), self.strategy_parameters.get("ALLOW_LOWER_PRICE_BREACH", 0)]:
            check_argument(param, "extern_function", lambda x: isinstance(x, int) and x in [0, 1], f"ALLOW_*_PRICE_BREACH flags should be either 0 (False) or 1 (True)")

    def initialize(self):
        super().initialize()

        # Reset main orders for calls and puts
        self.child_instrument_main_orders_ce, self.child_instrument_main_orders_pe = {}, {}
        self.flag_lower_breach_possible_reentry = self.flag_upper_breach_possible_reentry = False
        self.base_price_post_breach = None

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta, executed_tradingsymbol_suffix = [], [], set()

        for base_instrument in instruments_bucket:
            self.logger.info(
                f"Checking entry/re-entry conditions for base instrument: {base_instrument} | "
                f"Determining OTM option instruments and verifying if CE/PE orders are already placed."
            )
            # Define a list of tuples for managing legs, their types, and relevant orders
            leg_wise_list = [
                ("ce_buy_leg", 'CE', OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_buy_ce_leg, 'BUY',
                 self.child_instrument_main_orders_ce[base_instrument].get("ce_buy_leg") if self.child_instrument_main_orders_ce.get(base_instrument) else None),
                ("ce_sell_leg", 'CE', OptionsStrikeDirection.ATM.value, 0, 'SELL', self.child_instrument_main_orders_ce[base_instrument].get("ce_sell_leg") if self.child_instrument_main_orders_ce.get(base_instrument) else None),
                ("pe_buy_leg", 'PE', OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_buy_pe_leg, 'BUY',
                 self.child_instrument_main_orders_pe[base_instrument].get("pe_buy_leg") if self.child_instrument_main_orders_pe.get(base_instrument) else None),
                ("pe_sell_leg", 'PE', OptionsStrikeDirection.ATM.value, 0, 'SELL', self.child_instrument_main_orders_pe[base_instrument].get("pe_sell_leg") if self.child_instrument_main_orders_pe.get(base_instrument) else None)
            ]

            current_underlying_price = self.broker.get_ltp(base_instrument)

            # Calculate re-entry conditions based on the latest price
            flag_upper_re_entry = (self.flag_upper_breach_possible_reentry and (self.base_price_post_breach - current_underlying_price) >= self.price_breach_upper_offset)
            flag_lower_re_entry = (self.flag_lower_breach_possible_reentry and (current_underlying_price - self.base_price_post_breach) >= self.price_breach_lower_offset)

            # Check if there are no active orders (PE/CE legs), re-entry check has been triggered
            flag_empty_orders = (not self.child_instrument_main_orders_pe and not self.child_instrument_main_orders_ce)
            flag_re_entry_triggered = (self.flag_upper_breach_possible_reentry or self.flag_lower_breach_possible_reentry)

            # Proceed only if no open orders or if the re-entry conditions are met with existing orders
            if flag_empty_orders and (flag_upper_re_entry or flag_lower_re_entry or not flag_re_entry_triggered):
                for leg, tradingsymbol_suffix, strike_direction, no_of_strikes, action, main_order in leg_wise_list:
                    if tradingsymbol_suffix not in executed_tradingsymbol_suffix:
                        self.options_instruments_set_up_all_expiries(base_instrument, tradingsymbol_suffix, current_underlying_price)
                        executed_tradingsymbol_suffix.add(tradingsymbol_suffix)

                    child_instrument = self.get_child_instrument_details(base_instrument, tradingsymbol_suffix, strike_direction, no_of_strikes)  # Retrieve child base_instrument details for the given base_instrument
                    self.instruments_mapper.add_mappings(base_instrument, child_instrument)  # Maps each base_instrument to its child in the instruments' mapper for further processing.
                    selected_instruments.append(child_instrument)
                    meta.append({"leg": leg, "action": action, "base_instrument": base_instrument, "tradingsymbol_suffix": tradingsymbol_suffix})

            if flag_re_entry_triggered:
                self.flag_lower_breach_possible_reentry = self.flag_upper_breach_possible_reentry = False  # Resets upper and lower price breach conditions after re-entry is triggered and acted upon

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):

        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        self.base_price_post_breach = self.broker.get_ltp(base_instrument)  # Initializes base reference price with trade entry price; updated only after breach.
        if meta['action'] == 'BUY':
            _order = self.broker.BuyOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)
        else:
            _order = self.broker.SellOrderRegular(instrument=child_instrument, order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)

        if check_order_placed_successfully(_order):
            if meta["tradingsymbol_suffix"] == "CE":
                self.child_instrument_main_orders_ce.setdefault(meta['base_instrument'], {})[meta['leg']] = _order
            else:
                self.child_instrument_main_orders_pe.setdefault(meta['base_instrument'], {})[meta['leg']] = _order
        else:
            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other legs, if possible and stopping strategy.')
            self.exit_all_positions_for_base_instrument(base_instrument)
            raise ABSystemExit

        return _order

    def check_exit_condition(self, base_instrument):
        """ Determines if the strategy should exit based on price breach conditions. Returns True if exit condition is met. """

        current_underlying_price = self.broker.get_ltp(base_instrument)
        self.flag_upper_breach_possible_reentry = current_underlying_price - self.base_price_post_breach >= self.price_breach_upper_offset if self.price_breach_upper_offset else False
        self.flag_lower_breach_possible_reentry = self.base_price_post_breach - current_underlying_price >= self.price_breach_lower_offset if self.price_breach_lower_offset else False

        if self.flag_upper_breach_possible_reentry or self.flag_lower_breach_possible_reentry:
            breach_type_str = 'Upper' if self.flag_upper_breach_possible_reentry else 'Lower'
            self.logger.info(f'{breach_type_str} price thresholds breached. Exiting current positions for all legs. Checking re-entry condition in next candle.')
            self.base_price_post_breach = current_underlying_price  # Current ltp becomes new base reference price in case of breach
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
                if all(check_order_complete_status(order) for order in main_orders) and self.check_exit_condition(base_instrument):
                    selected_instruments_bucket.extend(order.instrument for order in main_orders if order)
                    meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(main_orders))

        return selected_instruments_bucket, meta

    def exit_all_positions_for_base_instrument(self, base_instrument):
        child_instrument_main_orders = [*self.child_instrument_main_orders_ce.get(base_instrument, {}).values(), *self.child_instrument_main_orders_pe.get(base_instrument, {}).values()]

        for order in filter(None, child_instrument_main_orders):  # Exit all active positions for the base instrument.
            order.exit_position()

        # Remove references to the base instrument after exiting both CE and PE orders.
        self.child_instrument_main_orders_ce.pop(base_instrument, None)
        self.child_instrument_main_orders_pe.pop(base_instrument, None)

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

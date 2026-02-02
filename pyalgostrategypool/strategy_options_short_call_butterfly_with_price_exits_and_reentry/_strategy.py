"""
   Strategy Description:
       The Short Call Butterfly strategy is a defined-risk, three-strike options setup constructed using call options only.
       It involves buying one in-the-money Call, selling two at-the-money Calls, and buying one further out-of-the-money Call.
       This strategy is typically used when moderate price movement away from the central strike is expected, aiming to profit from controlled
       directional expansion while maintaining limited risk. In addition to the defined payoff structure, the strategy employs target profit exits,
       stop-loss protection, price-level breach exits and controlled re-entry logic to actively manage positions before expiration.

    Strategy Resources:
        - Strategy-specific docs: https://algobulls.github.io/pyalgotrading/strategies/options_short_call_butterfly_with_price_exits_and_reentry/
        - General strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
"""


class StrategyOptionsShortCallButterflyPriceExitsReentry(StrategyOptionsBase):
    """ Short Call Butterfly Strategy price level exits, target/stoploss and reentry. """

    name = "Strategy Options Short Call Butterfly With Price Exits and Re-Entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Number of strikes away from ATM for the long wings (ITM & OTM sells)
        self.no_of_otm_strikes_buy_ce_leg = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_BUY_CALL_LEG", 2)
        self.no_of_itm_strikes_buy_ce_leg = self.strategy_parameters.get("NUMBER_OF_ITM_STRIKES_BUY_CALL_LEG", 2)

        # Reentry/Stoploss/Target parameters
        self.reentry_limit = self.strategy_parameters.get("REENTRY_LIMIT", 1)
        self.stoploss_percentage = self.strategy_parameters.get("STOPLOSS_PERCENTAGE", 10)
        self.target_percentage = self.strategy_parameters.get("TARGET_PERCENTAGE", 20)

        # Price offsets used to detect breach and trigger exit; enabled via flags
        self.price_breach_upper_offset = self.strategy_parameters.get("PRICE_BREACH_UPPER_OFFSET", 100) if self.strategy_parameters.get("ALLOW_UPPER_PRICE_BREACH", 0) == 1 else None
        self.price_breach_lower_offset = self.strategy_parameters.get("PRICE_BREACH_LOWER_OFFSET", 100) if self.strategy_parameters.get("ALLOW_LOWER_PRICE_BREACH", 0) == 1 else None

        # Internal variables and placeholders
        self.base_instrument_price_at_entry = None
        self.child_instrument_main_orders = None  # Tracks Call orders
        self.entry_net_premium = self.stoploss_premium = self.target_premium = None
        self.flag_lower_breach_possible_reentry = self.flag_upper_breach_possible_reentry = None
        self.reentry_left = None

        self.validate_parameters()

    def validate_parameters(self):

        # Validate parameters
        for param in (self.price_breach_upper_offset, self.price_breach_lower_offset, self.target_percentage, self.stoploss_percentage):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Parameter should be a non-negative number (>= 0.0)")

        for param in (self.no_of_otm_strikes_buy_ce_leg, self.no_of_itm_strikes_buy_ce_leg):
            check_argument(param, "extern_function", is_positive_int, "NUMBER_OF_*_STRIKES_CALL_LEG parameters should be positive integers (> 0)")

        for param in (self.strategy_parameters.get("ALLOW_UPPER_PRICE_BREACH", 0), self.strategy_parameters.get("ALLOW_LOWER_PRICE_BREACH", 0)):
            check_argument(param, "extern_function", lambda x: isinstance(x, int) and x in [0, 1], f"ALLOW_*_PRICE_BREACH flags should be either 0 (False) or 1 (True)")

        check_argument(self.reentry_limit, "extern_function", is_nonnegative_int, "REENTRY_LIMIT should be a non-negative integer (>= 0)")

    def initialize(self):
        super().initialize()

        # Reset strategy state for new trading day
        self.base_instrument_price_at_entry = {}
        self.child_instrument_main_orders = {}
        self.entry_net_premium, self.stoploss_premium, self.target_premium = {}, {}, {}
        self.flag_upper_breach_possible_reentry, self.flag_lower_breach_possible_reentry = {}, {}
        self.reentry_left = {}

        # Initializes reentries and main_order dictionaries
        for instrument in tls.TLS.instruments_bucket:
            self.child_instrument_main_orders[instrument] = {}

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for base_instrument in instruments_bucket:

            self.logger.info(
                f"Checking entry/re-entry conditions for base instrument: {base_instrument} | "
                f"Determining option instruments and verifying if CE orders are already placed."
            )

            # Define a list of tuples for managing legs, their types, and relevant orders
            leg_wise_list = [
                ("leg_ce_buy_otm", 'CE', OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_buy_ce_leg, 'BUY',
                 self.child_instrument_main_orders[base_instrument].get("leg_ce_buy_otm")),
                ("leg_ce_sell_atm", 'CE', OptionsStrikeDirection.ATM.value, 0, 'SELL',
                 self.child_instrument_main_orders[base_instrument].get("leg_ce_sell_atm_one")),
                ("leg_ce_buy_itm", 'CE', OptionsStrikeDirection.ITM.value, self.no_of_itm_strikes_buy_ce_leg, 'BUY',
                 self.child_instrument_main_orders[base_instrument].get("leg_ce_buy_itm"))
            ]

            # Initialize re-entry count for this base instrument, else decrement remaining re-entries
            current_underlying_price = self.broker.get_ltp(base_instrument)
            if not self.reentry_left.get(base_instrument):
                self.reentry_left[base_instrument] = self.reentry_limit
            else:
                self.reentry_left[base_instrument] -= 1

            # Proceed only if no open orders or if there are reentries left
            if not self.child_instrument_main_orders.get(base_instrument) and self.reentry_left[base_instrument] > 0:
                for leg, tradingsymbol_suffix, strike_direction, no_of_strikes, action, main_order in leg_wise_list:
                    self.options_instruments_set_up_all_expiries(base_instrument, tradingsymbol_suffix, current_underlying_price)
                    child_instrument = self.get_child_instrument_details(base_instrument, tradingsymbol_suffix, strike_direction, no_of_strikes)  # Retrieve child base_instrument details for the given base_instrument
                    self.instruments_mapper.add_mappings(base_instrument, child_instrument)  # Maps each base_instrument to its child in the instruments' mapper for further processing.
                    selected_instruments.append(child_instrument)
                    meta.append({"leg": leg, "action": action, "base_instrument": base_instrument})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        _order = None
        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        ltp_current = self.broker.get_ltp(child_instrument)
        quantity = self.number_of_lots * child_instrument.lot_size if meta['leg'] != "leg_ce_sell_atm" else 2 * child_instrument.lot_size * self.number_of_lots
        _order = self.broker.OrderRegular(
            child_instrument, meta['action'], order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.LIMIT, position=BrokerExistingOrderPositionConstants.ENTER, price=ltp_current, quantity=quantity
        )
        if check_order_placed_successfully(_order) and check_order_complete_status(_order):
            self.base_instrument_price_at_entry[base_instrument] = self.broker.get_ltp(base_instrument)  # Initializes base reference price with trade entry price; updated only after each exit.
            self.child_instrument_main_orders[base_instrument][meta['leg']] = _order
        else:
            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other legs...')
            self.exit_all_positions_for_base_instrument(base_instrument)
        return _order

    def check_exit_condition(self, base_instrument):
        """ Determines if the strategy should exit based on price breach conditions. Returns True if exit condition is met. """
        ott_multiplier = {BrokerOrderTransactionTypeConstants.BUY: 1, BrokerOrderTransactionTypeConstants.SELL: -1}
        exit_triggered = False

        # Price level breach check
        current_underlying_price = self.broker.get_ltp(base_instrument)
        flag_upper_breach_possible_reentry = current_underlying_price - self.base_instrument_price_at_entry.get(base_instrument) >= self.price_breach_upper_offset if self.price_breach_upper_offset else False
        flag_lower_breach_possible_reentry = current_underlying_price - self.base_instrument_price_at_entry.get(base_instrument) <= - self.price_breach_lower_offset if self.price_breach_lower_offset else False

        if flag_upper_breach_possible_reentry or flag_lower_breach_possible_reentry:
            breach_type_str = 'Upper' if self.flag_upper_breach_possible[base_instrument] else 'Lower'
            self.logger.info(f'{breach_type_str} price thresholds breached. Exiting current positions for all legs. Checking reentry condition in next candle...')
            exit_triggered = True

        # Target/Stoploss Check
        current_net_premium = sum([ott_multiplier[order.order_transaction_type] * self.broker.get_ltp(order.instrument) for order in self.child_instrument_main_orders[base_instrument].values()])
        if not self.entry_net_premium.get(base_instrument):
            self.entry_net_premium[base_instrument] = sum(
                ott_multiplier[order.order_transaction_type] * order.entry_price for order in self.child_instrument_main_orders[base_instrument].values()) if not self.entry_net_premium else self.entry_net_premium
            self.stoploss_premium[base_instrument] = self.entry_net_premium[base_instrument] * (1 + self.stoploss_percentage / 100)
            self.target_premium[base_instrument] = self.entry_net_premium[base_instrument] * (1 - self.target_percentage / 100)

        self.logger.debug(f"For {base_instrument}: "
                          f"Net Entry Premium: {self.entry_net_premium[base_instrument]} | Current Net Premium: {current_net_premium} | Stoploss Threshold: {self.stoploss_premium[base_instrument]:.2f} | Target Threshold: {self.target_premium[base_instrument]:.2f}")

        target_profit_condition = current_net_premium < self.target_premium[base_instrument]
        if target_profit_condition:
            self.logger.debug(f"For {base_instrument}: Target profit reached - Current Net Premium ({current_net_premium}) dropped below Target Threshold ({self.target_premium[base_instrument]:.2f}). Exiting position...")
            exit_triggered = True

        stop_loss_condition = current_net_premium > self.stoploss_premium[base_instrument]
        if stop_loss_condition:
            self.logger.debug(f"For {base_instrument}: Stoploss triggered - Current Net Premium ({current_net_premium}) exceeded Stoploss Threshold ({self.stoploss_premium[base_instrument]:.2f}). Exiting position...")
            exit_triggered = True

        if exit_triggered:
            self.base_instrument_price_at_entry[base_instrument] = current_underlying_price  # Current ltp becomes new base reference price in case of breach
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

                # Checks if all CE orders are complete and if exit conditions are met.
                main_orders = self.child_instrument_main_orders[base_instrument].values()
                if all(check_order_complete_status(order) for order in main_orders) and self.check_exit_condition(base_instrument):
                    selected_instruments_bucket.extend(order.instrument for order in main_orders if order)
                    meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(main_orders))

        return selected_instruments_bucket, meta

    def exit_all_positions_for_base_instrument(self, base_instrument):

        for order in filter(None, self.child_instrument_main_orders.get(base_instrument, {}).values()):  # Exit all active positions for the base instrument.
            child_instrument = order.instrument
            ltp_current = self.broker.get_ltp(child_instrument)
            quantity = order.quantity
            action = BrokerOrderTransactionTypeConstants.SELL if order.order_transaction_type == BrokerOrderTransactionTypeConstants.BUY else BrokerOrderTransactionTypeConstants.BUY
            _order = self.broker.OrderRegular(
                child_instrument, action, order_code=self.order_code, position=BrokerExistingOrderPositionConstants.EXIT, order_variety=BrokerOrderVarietyConstants.LIMIT, price=ltp_current, quantity=quantity,
                related_order=order
            )

        # Remove references to the base instrument after exiting all orders.
        self.child_instrument_main_orders[base_instrument] = {}
        self.target_premium.pop(base_instrument, None)
        self.stoploss_premium.pop(base_instrument, None)
        self.entry_net_premium.pop(base_instrument, None)

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

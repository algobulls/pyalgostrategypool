"""
Strategy Description:
   Implements a Long Call Diagonal Spread: buy ATM Call (longer expiry) + sell a higher-strike Call (nearer expiry).
   Adds an absolute stop-loss, a trailing stop mechanism, and optional controlled re-entries.
   Exit decisions use spread premium (buy - sell); trailing stop and hard stop are enforced.
Strategy Resources:
   - Strategy-specific docs: https://algobulls.github.io/pyalgotrading/strategies/options_bull_call_spread_with_target_stops_and_reentry/
   - General strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
"""


class StrategyOptionsLongCallDiagonalSpreadWithTrailingStoplossReentry(StrategyOptionsBase):
    """
    Long Call Diagonal Spread with stops and re-entry management:
       - Uses ATM buy (longer expiry) and OTM sell (nearer expiry) legs and tracks net premium (buy - sell).
       - Supports absolute stoploss, trailing stoploss and controlled re-entry count.
    """

    name = "Strategy Options Long Call Diagonal Spread With TSL & Reentry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.no_of_otm_strikes_leg_sell = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_SELL_LEG", 4)
        self.re_entry_limit = self.strategy_parameters.get("RE_ENTRY_LIMIT", 4)
        self.stoploss_percentage = self.strategy_parameters.get("STOPLOSS_PERCENTAGE", 15)
        self.tsl_percentage = self.strategy_parameters.get("TRAILING_STOPLOSS_PERCENTAGE", 20)

        # Internal variables and placeholders
        self.child_instrument_main_orders = None  # Tracks Call orders
        self.highest = None
        self.spread_current = None
        self.spread_entry = None
        self.stoploss_premium = None
        self.re_entry_count = None
        self.trailing_stop = None
        self.trailing_stoploss_activated = None

        self.validate_parameters()

    def validate_parameters(self):
        """ Validates required strategy parameters. """
        check_argument(
            self.strategy_parameters, "extern_function", lambda _: len(_) >= 4,
            err_message=(
                "Need 4 parameters for this strategy: \n"
                "(1) NUMBER_OF_OTM_STRIKES_SELL_LEG \n"
                "(2) STOPLOSS_PERCENTAGE \n"
                "(3) TRAILING_STOPLOSS_PERCENTAGE \n"
                "(4) RE_ENTRY_LIMIT"
            )
        )

        # Validate numeric strategy parameters
        for param in (self.re_entry_limit, self.no_of_otm_strikes_leg_sell):
            check_argument(param, "extern_function", is_positive_int, "Value should be a positive integer")

        # Validate percentage strategy parameters
        for param in (self.stoploss_percentage, self.tsl_percentage,):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >0")

    def initialize(self):
        super().initialize()

        # Reset main orders, spread and stop levels; zero re-entry counts
        self.child_instrument_main_orders = {}
        self.highest = self.spread_current = self.spread_entry = self.stoploss_premium = self.trailing_stop = None
        self.re_entry_count = {}

    def _check_exit_conditions(self, child_leg_orders_dict):
        """
        Evaluate exit rules for the Long Call Diagonal Spread.
        Checks:
        - Absolute stop-loss: exit if spread drops below the stop premium threshold.
        - Trailing stop-loss: activate after sufficient upside and update trailing stop.
        """

        # Retrieve latest traded prices (LTP) for both legs
        ltp_leg_buy = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.BUY].instrument)
        ltp_leg_sell = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.SELL].instrument)

        # Initialize levels at first check after entry
        if not self.spread_entry:
            entry_price_leg_buy = child_leg_orders_dict[BrokerOrderTransactionTypeConstants.BUY].entry_price
            entry_price_leg_sell = child_leg_orders_dict[BrokerOrderTransactionTypeConstants.SELL].entry_price
            self.spread_entry = entry_price_leg_buy - entry_price_leg_sell  # spread at entry
            self.stoploss_premium = self.spread_entry * (1 - self.stoploss_percentage / 100)
            self.highest = self.spread_entry
            self.trailing_stop = self.stoploss_premium

        # Current spread price
        self.spread_current = ltp_leg_buy - ltp_leg_sell

        # Absolute Stoploss Check
        if self.spread_current < self.stoploss_premium:
            self.logger.debug(f"Absolute stoploss triggered: Current Net Premium: {self.spread_current:.2f} | Absolute Stoploss threshold: {self.stoploss_premium:.2f} | "
                              f"Exiting positions...")

            # Reset so next entry can be reinitialized
            self.highest = self.spread_entry = self.trailing_stop = self.trailing_stoploss_activated = None

            return True

        # Trailing Stop-loss (TSL) check
        # Activate trailing stop only after spread moves by at least trailing % above entry.
        # Update trailing stop whenever current spread exceeds previous high
        if self.spread_current > self.highest:
            if not self.trailing_stoploss_activated:
                self.logger.info(f"Trailing Stoploss Activated")
                self.trailing_stoploss_activated = True
            new_stop = self.spread_current * (1 - self.tsl_percentage / 100)
            self.logger.info(f"Trailing Stoploss Adjusted: Current Spread price: {self.spread_current:.2f} | "
                             f"Previous Spread price: {self.highest:.2f} |"
                             f"Old Stop: {self.trailing_stop:.2f} | "
                             f"New Stop: {new_stop:.2f} | "
                             f"(Trail % = {self.tsl_percentage})")
            self.trailing_stop = new_stop
            self.highest = self.spread_current
            self.stoploss_premium = self.trailing_stop

        return False

    def exit_all_positions_for_base_instrument(self, base_instrument):
        for order in filter(None, self.child_instrument_main_orders.get(base_instrument, {}).values()):  # Exit all active positions for the base instrument.
            order.exit_position()

        # Remove references to the base instrument after exiting CE orders.
        self.child_instrument_main_orders.pop(base_instrument, None)

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            self.logger.debug(f"Checking entry conditions for base instrument: {instrument} | Determining ATM/OTM option instruments and verifying if CE orders are already placed.")

            # Skip the base instrument if active order already exists
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
                self.re_entry_count[instrument] = self.re_entry_count.get(instrument, 0) + 1

            # leg_wise_list structure:
            # (action, strike_direction, no_of_strikes, expiry_index)
            #
            # expiry_index:
            #   0 -> selects the *first* expiry in the generated list (typically the current / near-month expiry)
            #   1 -> selects the *second* expiry in the generated list (typically the next / far-month expiry)
            #
            # These indices correspond to the order of expiry dates returned by:
            # self.options_instruments_set_up_all_expiries(...)
            # which usually produces: [current_month_expiry, next_month_expiry]
            leg_wise_list = [
                (BrokerOrderTransactionTypeConstants.BUY, OptionsStrikeDirection.ATM.value, 0, 1),
                (BrokerOrderTransactionTypeConstants.SELL, OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_leg_sell, 0)
            ]

            for action, strike_direction, no_of_strikes, expiry in leg_wise_list:
                self.options_instruments_set_up_all_expiries(instrument, 'CE', base_instrument_ltp)  # Set up option instruments for available expiries
                child_instrument = self.get_child_instrument_details(instrument, 'CE', strike_direction, no_of_strikes, expiry)  # Retrieve ATM child instrument details for the given instrument

                # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                self.instruments_mapper.add_mappings(instrument, child_instrument)

                selected_instruments.append(child_instrument)
                meta.append({"action": action})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        _order = None
        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        main_orders = self.child_instrument_main_orders.get(base_instrument)
        action_other_leg = BrokerOrderTransactionTypeConstants.BUY if meta['action'] == BrokerOrderTransactionTypeConstants.SELL else BrokerOrderTransactionTypeConstants.SELL

        # If the opposite leg is expected but not present, abort entry and clear any partial state for this base instrument
        if main_orders and not main_orders.get(action_other_leg):
            self.child_instrument_main_orders.pop(base_instrument, None)
            return _order

        # Attempt to place market order for the current leg
        try:
            _order = self.broker.OrderRegular(instrument=child_instrument, order_transaction_type=meta['action'], order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)
        except Exception as e:
            # Order placement failure is logged; downstream validation will handle cleanup
            self.logger.debug(f"No orders placed due to the following error: {e}")

        # Store details of orders
        self.child_instrument_main_orders.setdefault(base_instrument, {})[meta['action']] = _order

        if not check_order_placed_successfully(_order):
            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other leg, if possible and stopping further entries..')
            if _order:
                self.exit_all_positions_for_base_instrument(base_instrument)
            self.execution_complete = True  # Stop further strategy execution for safety
        return _order

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments_bucket, meta, _base_instruments_processed_list = [], [], []

        for instrument in instruments_bucket:
            if self.instruments_mapper.is_child_instrument(instrument):
                base_instrument = self.instruments_mapper.get_base_instrument(instrument)

                if base_instrument in _base_instruments_processed_list:
                    continue  # Skip if already processed

                _base_instruments_processed_list.append(base_instrument)

                # Check if CE orders are complete and evaluate exit conditions.
                child_leg_orders_dict = self.child_instrument_main_orders.get(base_instrument)
                if child_leg_orders_dict:
                    if all(check_order_complete_status(order) for order in child_leg_orders_dict.values()) and (self._check_exit_conditions(child_leg_orders_dict)):
                        selected_instruments_bucket.extend(order.instrument for order in child_leg_orders_dict.values() if order)
                        meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(self.child_instrument_main_orders))

        return selected_instruments_bucket, meta

    def strategy_exit_position(self, candle, instrument, meta):
        self.exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True

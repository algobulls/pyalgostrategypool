"""
Strategy Description:
    The Short Jade Lizard strategy is a defined-risk options strategy constructed using both call and put options.
    It involves selling an out-of-the-money Put, selling an out-of-the-money Call, and buying a further out-of-the-money Call
    for upside risk protection. The strategy is typically used when a neutral-to-bullish market outlook is expected,
    aiming to profit from time decay and stable price action while eliminating upside risk beyond the call spread width.
    In addition to the defined payoff structure, the strategy employs net premium target exits, stop-loss protection,
    and controlled re-entry logic to actively manage positions before expiration.
"""

from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection


class StrategyOptionsShortJadeLizardWithNetValueExitsReentry(StrategyOptionsBase):
    """ Short Jade Lizard Strategy with net-premium target/stop-loss exits and re-entry. """

    name = "Strategy Options Short Jade Lizard with Net Value Exits and Re-Entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Number of OTM strikes away from ATM for all wings
        self.number_of_strikes_otm_ce_buy = self.strategy_parameters.get('NUMBER_OF_STRIKES_OTM_CE_BUY', 2)
        self.number_of_strikes_otm_ce_sell = self.strategy_parameters.get('NUMBER_OF_STRIKES_OTM_CE_SELL', 2)
        self.number_of_strikes_otm_pe_sell = self.strategy_parameters.get('NUMBER_OF_STRIKES_OTM_PE_SELL', 4)

        # Reentry/Stoploss/Target parameters
        self.reentry_count = self.strategy_parameters.get("REENTRY_COUNT", 0)
        self.stoploss_percentage = self.strategy_parameters.get("STOPLOSS_PERCENTAGE", 2)
        self.target_percentage = self.strategy_parameters.get("TARGET_PERCENTAGE", 5)

        # Internal variables and placeholders
        self.child_instrument_main_orders = None  # Tracks Call and Put orders
        self.entry_net_premium = self.target_premium = self.stoploss_premium = None
        self.reentry_left = None
        self.execution_complete = None

        self.validate_parameters()

    def validate_parameters(self):
        """ Validates required strategy parameters. """

        # Validate number of options strikes and re-entry parameters
        for param in (self.number_of_strikes_otm_ce_sell, self.number_of_strikes_otm_ce_buy, self.number_of_strikes_otm_pe_sell, self.reentry_count):
            check_argument(param, "extern_function", is_positive_int, "NUMBER_OF_STRIKES_OTM_* or REENTRY_COUNT parameter should be positive integers (> 0)")

        # Validate target and stoploss percentage parameters
        for param in (self.target_percentage, self.stoploss_percentage):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >0.0")

    def initialize(self):
        super().initialize()

        # Reset strategy state for new trading day
        self.child_instrument_main_orders = {}
        self.entry_net_premium, self.stoploss_premium, self.target_premium, self.reentry_left = {}, {}, {}, {}
        self.execution_complete = False

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            self.logger.debug(
                f"Checking entry conditions for base instrument: {instrument} | "
                f"Determining OTM option instruments and verifying if CE/PE orders are already placed."
            )

            # Define a list of tuples for managing legs, their types, and relevant orders
            leg_wise_list = [
                ("leg_ce_sell_otm", "CE", self.number_of_strikes_otm_ce_sell, "SELL", self.child_instrument_main_orders.get(instrument, {}).get('leg_ce_sell_otm')),
                ("leg_ce_buy_otm", "CE", self.number_of_strikes_otm_ce_buy, "BUY", self.child_instrument_main_orders.get(instrument, {}).get('leg_ce_buy_otm')),
                ("leg_pe_sell_otm", "PE", self.number_of_strikes_otm_pe_sell, "SELL", self.child_instrument_main_orders.get(instrument, {}).get('leg_pe_sell_otm'))
            ]

            # Initialize re-entry count for this base instrument, else decrement remaining re-entries
            if self.reentry_left.get(instrument) is None:
                self.reentry_left[instrument] = self.reentry_count
            elif self.reentry_left[instrument] > 0:
                self.reentry_left[instrument] -= 1
            else:
                self.logger.debug(f"No more reentries left for {instrument}")
                if not any(self.reentry_left.values()):
                    self.execution_complete = True
                continue

            # Proceed only if no open orders or if there are reentries left
            if not self.child_instrument_main_orders.get(instrument):
                current_underlying_price = self.broker.get_ltp(instrument)
                for leg, tradingsymbol_suffix, no_of_strikes, action, main_order in leg_wise_list:
                    self.options_instruments_set_up_all_expiries(instrument, tradingsymbol_suffix, current_underlying_price)
                    child_instrument = self.get_child_instrument_details(instrument, tradingsymbol_suffix, OptionsStrikeDirection.OTM.value, no_of_strikes)  # Retrieve child base_instrument details for the given base_instrument
                    self.instruments_mapper.add_mappings(instrument, child_instrument)  # Maps each base_instrument to its child in the instruments' mapper for further processing.
                    selected_instruments.append(child_instrument)
                    meta.append({"leg": leg, "action": action, "base_instrument": instrument})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        _order = None
        child_instrument = instrument
        base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
        ltp_current = self.broker.get_ltp(child_instrument)
        quantity = self.number_of_lots * child_instrument.lot_size
        _order = self.broker.OrderRegular(
            child_instrument, meta['action'], order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.LIMIT, position=BrokerExistingOrderPositionConstants.ENTER, price=ltp_current, quantity=quantity
        )
        if check_order_placed_successfully(_order) and check_order_complete_status(_order):
            self.child_instrument_main_orders.setdefault(base_instrument, {})[meta['leg']] = _order
        else:
            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other legs...')
            self.exit_all_positions_for_base_instrument(base_instrument)
        return _order

    def check_exit_condition(self, base_instrument, main_orders):
        """ Determines if the strategy should exit based on Target/Stoploss Net Premium. Returns True if exit condition is met. """
        ott_multiplier = {"BUY": 1, "SELL": -1}

        # Target/Stoploss Net Premium Check
        current_net_premium = sum([ott_multiplier[order.order_transaction_type.value] * self.broker.get_ltp(order.instrument) for order in main_orders])
        if not self.entry_net_premium.get(base_instrument):
            self.entry_net_premium[base_instrument] = sum(
                ott_multiplier[order.order_transaction_type.value] * order.entry_price for order in main_orders) if not self.entry_net_premium else self.entry_net_premium
            self.stoploss_premium[base_instrument] = self.entry_net_premium[base_instrument] * (1 + self.stoploss_percentage / 100)
            self.target_premium[base_instrument] = self.entry_net_premium[base_instrument] * (1 - self.target_percentage / 100)

        self.logger.debug(f"For {base_instrument}: "
                          f"Net Entry Premium: {self.entry_net_premium[base_instrument]:.2f} | Current Net Premium: {current_net_premium:.2f} | Stoploss Threshold: {self.stoploss_premium[base_instrument]:.2f} | Target Threshold: {self.target_premium[base_instrument]:.2f}")

        target_profit_condition = current_net_premium > self.target_premium[base_instrument]
        if target_profit_condition:
            self.logger.debug(f"For {base_instrument}: Net Premium Target profit reached - Current Net Premium ({current_net_premium:.2f}) dropped below Target Threshold ({self.target_premium[base_instrument]:.2f}). Exiting positions...")
            return True

        stop_loss_condition = current_net_premium < self.stoploss_premium[base_instrument]
        if stop_loss_condition:
            self.logger.debug(f"For {base_instrument}: Net Premium Stoploss triggered - Current Net Premium ({current_net_premium:.2f}) exceeded Stoploss Threshold ({self.stoploss_premium[base_instrument]:.2f}). Exiting positions...")
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
                main_orders = self.child_instrument_main_orders.get(base_instrument, {}).values()

                # Check if both CE and PE orders are complete and if exit conditions are met.
                if all(check_order_complete_status(order) for order in main_orders) and self.check_exit_condition(base_instrument, main_orders):
                    selected_instruments_bucket.extend(order.instrument for order in main_orders if order)
                    meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(main_orders))

        return selected_instruments_bucket, meta

    def exit_all_positions_for_base_instrument(self, base_instrument):

        for order in filter(None, self.child_instrument_main_orders.get(base_instrument, {}).values()):  # Exit all active positions for the base instrument.
            child_instrument = order.instrument
            ltp_current = self.broker.get_ltp(child_instrument)
            quantity = order.quantity
            action = "SELL" if order.order_transaction_type == "BUY" else "BUY"
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

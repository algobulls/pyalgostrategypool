"""
Strategy Description:
    This strategy uses Options Greek (delta) values to identify if the selected strike is suitable for order placement.
    It checks whether the strike's delta lies within a specified range (delta_min to delta_max) to qualify for entry.
    Once the entry order is placed and complete, exit orders (target and stoploss) are placed based on percentage thresholds.
    It tracks and manages these orders, ensuring that if any one of the exits is honored, the other is cancelled.
"""


class StrategyOptionsGreekDeltaTargetStoplossIntraday(StrategyOptionsBase):
    name = 'Strategy Options Greek Delta Target Stoploss Intraday'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Parameters (currently set with default values, can be overridden from the Parameters Configuration Pane)
        self._transaction_type = self.strategy_parameters.get('TRANSACTION_TYPE', 1)  # BUY: 1 | SELL: 2
        self._tradingsymbol_suffix = self.strategy_parameters.get('TRADING_SYMBOL_SUFFIX', 2)  # CE: 1 | PE: 2
        self._strike_direction = self.strategy_parameters.get('STRIKE_DIRECTION', 1)  # ITM: 0| ATM: 1| OTM: 2
        self._number_of_strikes = self.strategy_parameters.get('NUMBER_OF_STRIKES', 0)

        self.greek_delta_lower_threshold = self.strategy_parameters.get('DELTA_MIN', 0.2)
        self.greek_delta_upper_threshold = self.strategy_parameters.get('DELTA_MAX', 0.25)
        self.risk_free_rate = self.strategy_parameters.get('RISK_FREE_RATE', 0.0682)
        self.volatility = self.strategy_parameters.get('VOLATILITY', 0.1469)

        self.stoploss_percentage = self.strategy_parameters.get('STOPLOSS_PERCENTAGE', 5)
        self.target_percentage = self.strategy_parameters.get('TARGET_PERCENTAGE', 20)

        # Maps
        self.transaction_type_map = {1: "BUY", 2: "SELL"}
        self.tradingsymbol_suffix_map = {1: "CE", 2: "PE"}
        self.strike_direction_map = {0: OptionsStrikeDirection.ITM, 1: OptionsStrikeDirection.ATM, 2: OptionsStrikeDirection.OTM}

        # Internal variables and placeholders
        self.main_order = None
        self.target_order = None
        self.stoploss_order = None
        self.number_of_allowed_expiry_dates = 1

        self._validate_parameters()

    def _validate_parameters(self):

        # Validate expiry dates
        if len(self.get_allowed_expiry_dates()) != self.number_of_allowed_expiry_dates:
            self.logger.info(f"Allowed expiry dates: {self.number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting...")
            raise ABSystemExit

        # Validate parameters
        for param in (self.greek_delta_lower_threshold, self.greek_delta_upper_threshold):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "DELTA_MIN and DELTA_MAX should be a non-negative number (>= 0.0)")

        for param in (self.stoploss_percentage, self.target_percentage, self.risk_free_rate, self.volatility):
            check_argument(param, "extern_function", is_positive_int_or_float, "STOPLOSS_PERCENTAGE, TARGET_PERCENTAGE, RISK_FREE_RATE and VOLATILITY should be a positive number (> 0.0)")

        check_argument(self._transaction_type, "extern_function", lambda x: x in [1, 2] and isinstance(x, int),
                       err_message='TRANSACTION_TYPE should be an integer with possible values 1: BUY or 2: SELL')

        check_argument(self._tradingsymbol_suffix, "extern_function", lambda x: x in [1, 2] and isinstance(x, int),
                       err_message='TRADING_SYMBOL_SUFFIX should be an integer with possible values 1: CE or 2: PE')

        check_argument(self._strike_direction, "extern_function", lambda x: x in [0, 1, 2] and isinstance(x, int),
                       err_message='STRIKE_DIRECTION should be an integer with possible values 0: ITM, 1: ATM or 2: OTM')

        check_argument(self._number_of_strikes, "extern_function", lambda x: 0 <= x <= 10 and isinstance(x, int),
                       err_message='NUMBER_OF_STRIKES should be an integer with possible values between 0 to 10')

    def initialize(self):
        super().initialize()
        self.main_order = {}
        self.target_order = {}
        self.stoploss_order = {}

    def _is_child_instrument_selected(self, base_instrument, child_instrument):
        ltp = self.broker.get_ltp(child_instrument)
        ltp_timestamp = self.get_current_timestamp()
        underlying_ltp = self.broker.get_ltp(base_instrument)
        current_timestamp = child_instrument.expiry
        greek_calculator = Greek()
        greek_data = greek_calculator.fetch_hist_data_greek(str(child_instrument), ltp_timestamp, underlying_ltp, ltp, self.risk_free_rate, current_timestamp)
        delta = abs(greek_data['delta'])
        return self.greek_delta_lower_threshold <= delta <= self.greek_delta_upper_threshold

    def _get_child_instrument_based_on_greek_delta(self, base_instrument, candle):
        tradingingsymbol_suffix, strike_direction, number_of_strikes = self._tradingsymbol_suffix, self._strike_direction, self._number_of_strikes
        ltp = self.broker.get_ltp(base_instrument)
        if self.tradingsymbol_suffix_map[tradingingsymbol_suffix] == "CE":
            self.options_instruments_set_up_all_expiries(base_instrument, "CE", ltp)
        else:
            self.options_instruments_set_up_all_expiries(base_instrument, "PE", ltp)

        child_instrument = self.get_child_instrument_details(base_instrument, self.tradingsymbol_suffix_map[tradingingsymbol_suffix], self.strike_direction_map[strike_direction], number_of_strikes)

        return child_instrument, self._is_child_instrument_selected(base_instrument, child_instrument)

    def _check_and_place_exit_order(self, base_instrument, main_order, exit_type, percentage, order_obj_data):
        existing_order = order_obj_data.get(base_instrument)
        if existing_order is not None:
            return

        if main_order.order_transaction_type is BrokerOrderTransactionTypeConstants.BUY:
            price = main_order.entry_price * (1 - (percentage / 100)) if exit_type == "stoploss" else main_order.entry_price * (1 + (percentage / 100))
            action = "SELL"
        else:
            price = main_order.entry_price * (1 + (percentage / 100)) if exit_type == "stoploss" else main_order.entry_price * (1 - (percentage / 100))
            action = "BUY"

        variety = BrokerOrderVarietyConstants.STOPLOSS_LIMIT if exit_type == "stoploss" else BrokerOrderVarietyConstants.LIMIT
        order = self.broker.OrderRegular(instrument=main_order.instrument, order_transaction_type=action, order_variety=variety, price=price, trigger_price=price, quantity=main_order.quantity, position=BrokerExistingOrderPositionConstants.EXIT,
                                         related_order=main_order)
        order_obj_data[base_instrument] = order

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            base_instrument = instrument
            if self.main_order.get(base_instrument) is None:
                child_instrument, is_selected = self._get_child_instrument_based_on_greek_delta(base_instrument, candle)
                if is_selected:
                    self.instruments_mapper.add_mappings(base_instrument, child_instrument)
                    selected_instruments.append(child_instrument)
                    meta.append({'base_instrument': base_instrument, 'action': self.transaction_type_map[self._transaction_type]})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        base_instrument = meta['base_instrument']
        self.main_order[base_instrument] = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            base_instrument = self.instruments_mapper.get_base_instrument(instrument)
            main_order = self.main_order.get(base_instrument)

            if check_order_complete_status(main_order):

                # Check and place exit orders
                self._check_and_place_exit_order(base_instrument, main_order, "stoploss", self.stoploss_percentage, self.stoploss_order)
                self._check_and_place_exit_order(base_instrument, main_order, "target", self.target_percentage, self.target_order)

                # Check which exit order is complete and cancel the other
                orders = {"stoploss": self.stoploss_order.get(base_instrument), "target": self.target_order.get(base_instrument)}
                for order_to_be_checked, order_to_be_cancelled in [("stoploss", "target"), ("target", "stoploss")]:
                    completed_order = orders[order_to_be_checked]
                    cancel_order = orders[order_to_be_cancelled]

                    if check_order_complete_status(completed_order):
                        cancel_order.cancel_order()
                        selected_instruments.append(instrument)
                        meta.append({'base_instrument': base_instrument, 'action': 'EXIT'})
                        break

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        base_instrument = meta['base_instrument']
        if meta['action'] == 'EXIT':
            self.main_order.pop(base_instrument, None)
            self.target_order.pop(base_instrument, None)
            self.stoploss_order.pop(base_instrument, None)
            return True

        return False

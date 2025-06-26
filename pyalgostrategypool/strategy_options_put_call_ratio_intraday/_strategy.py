"""
    Strategy Description:
        This strategy is a sentiment-driven options strategy that uses the Put-Call Ratio (PCR) to detect market bias.
        It calculates PCR using open interest (OI) from selected ITM+ATM+OTM Call and Put strikes. A high PCR indicates bearish market sentiment, while a low PCR indicates bullish sentiment.
        Based on PCR thresholds, the strategy selects ATM Call or Put options for directional intraday trades:
            - If PCR >= bearish threshold, it enters a CE ATM buy position (bearish reversal).
            - If PCR <= bullish threshold, it enters a PE ATM buy position (bullish reversal).
            - If PCR is neutral, no action is taken for that candle.
        The strategy manages a disciplined trend-switching behavior like this:
            - If a position for the opposite sentiment exists, it exits that before initiating the new one.
            - If a position for the same sentiment already exists, it avoids duplicate entries.
"""


class StrategyOptionsPutCallRatioIntraday(StrategyOptionsBase):
    name = 'Strategy Options Put Call Ratio Intraday'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.put_call_ratio_bullish = self.strategy_parameters.get('PUT_CALL_RATIO_BULLISH', 0.7)
        self.put_call_ratio_bearish = self.strategy_parameters.get('PUT_CALL_RATIO_BEARISH', 1.3)
        self.number_of_strikes = self.strategy_parameters.get('NUMBER_OF_STRIKES', 3)

        # Internal variables and placeholders
        self.main_order_map = None
        self.put_call_ratio_data = None
        self.number_of_allowed_expiry_dates = 1

        self._validate_parameters()

    def _validate_parameters(self):

        # Validate expiry dates
        if len(self.get_allowed_expiry_dates()) != self.number_of_allowed_expiry_dates:
            self.logger.info(f"Allowed expiry dates: {self.number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting...")
            raise ABSystemExit

        # Validate parameters
        for param in (self.put_call_ratio_bullish, self.put_call_ratio_bearish):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "PUT_CALL_RATIO_BULLISH and PUT_CALL_RATIO_BEARISH should be a non-negative number (>= 0.0)")

        check_argument(self.number_of_strikes, "extern_function", lambda x: 0 <= x <= 10 and isinstance(x, int),
                       err_message='NUMBER_OF_STRIKES should be an integer with possible values between 0 to 10')

    def initialize(self):
        super().initialize()
        self.main_order_map = {}
        self.put_call_ratio_data = {}

    def _get_child_instrument_based_on_pcr(self, instrument, candle):
        ltp = self.broker.get_ltp(instrument)
        data_key = f'{instrument.tradingsymbol}_{candle}'

        # Setup only if PCR value is not available for this candle
        if not data_key in self.put_call_ratio_data:
            self.options_instruments_set_up_all_expiries(instrument, "CE", ltp)
            self.options_instruments_set_up_all_expiries(instrument, "PE", ltp)
            self._get_put_call_ratio(instrument, candle)

        instruments_list_key = next(iter(self.instruments_ce_atm))
        if self.put_call_ratio_data[data_key] >= self.put_call_ratio_bearish:
            child_instrument, sentiment = self.instruments_ce_atm[instruments_list_key], "BEARISH"
        elif self.put_call_ratio_data[data_key] <= self.put_call_ratio_bullish:
            child_instrument, sentiment = self.instruments_pe_atm[instruments_list_key], "BULLISH"
        else:
            child_instrument, sentiment = None, None

        return child_instrument, sentiment

    def _get_sentiment_from_instrument(self, instrument):
        instrument = instrument.tradingsymbol
        if instrument.endswith('CE') or instrument.endswith('CE [LOCAL]'):
            return "BEARISH"
        elif instrument.endswith('PE') or instrument.endswith('PE [LOCAL]'):
            return "BULLISH"
        # Ideally, won't reach here
        else:
            raise NotImplementedError

    def _get_latest_oi(self, instrument):
        return self.get_historical_data(instrument)['oi'].iloc[-1]

    def _calculate_put_call_ratio(self, instruments_list_ce, instruments_list_pe):
        sum_oi_ce = sum(self._get_latest_oi(inst) for inst in instruments_list_ce)
        sum_oi_pe = sum(self._get_latest_oi(inst) for inst in instruments_list_pe)
        return sum_oi_pe / sum_oi_ce

    def _get_put_call_ratio(self, instrument, candle):
        data_key = f'{instrument.tradingsymbol}_{candle}'
        instruments_list_key = next(iter(self.instruments_ce_atm))
        if not data_key in self.put_call_ratio_data:
            self.put_call_ratio_data = {}
            instruments_list_ce = self.instruments_ce_itm[instruments_list_key][-self.number_of_strikes:] + [self.instruments_ce_atm[instruments_list_key]] + self.instruments_ce_otm[instruments_list_key][:self.number_of_strikes]
            instruments_list_pe = self.instruments_pe_itm[instruments_list_key][-self.number_of_strikes:] + [self.instruments_pe_atm[instruments_list_key]] + self.instruments_pe_otm[instruments_list_key][:self.number_of_strikes]
            self.put_call_ratio_data[data_key] = self._calculate_put_call_ratio(instruments_list_ce, instruments_list_pe)

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            base_instrument = instrument
            if self.main_order_map.get(base_instrument) is None:
                child_instrument, _ = self._get_child_instrument_based_on_pcr(base_instrument, candle)
                if child_instrument:
                    self.instruments_mapper.add_mappings(base_instrument, child_instrument)
                    selected_instruments.append(child_instrument)
                    meta.append({'base_instrument': base_instrument, 'action': 'BUY'})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        base_instrument = meta['base_instrument']
        self.main_order_map[base_instrument] = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            base_instrument = self.instruments_mapper.get_base_instrument(instrument)
            main_order = self.main_order_map.get(base_instrument)
            if main_order is not None:
                sentiment_current = self._get_sentiment_from_instrument(main_order.instrument)
                child_instrument, sentiment = self._get_child_instrument_based_on_pcr(base_instrument, candle)

                # Take exit action if we have the child instrument as well as the opposite sentiment
                if child_instrument and sentiment_current != sentiment:
                    selected_instruments.append(child_instrument)
                    meta.append({'base_instrument': base_instrument, 'action': 'EXIT'})

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        base_instrument = meta['base_instrument']
        if meta['action'] == 'EXIT':
            self.main_order_map[base_instrument].exit_position()
            self.main_order_map.pop(base_instrument, None)
            return True

        return False

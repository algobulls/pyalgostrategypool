class VolatilityTrendATR(StrategyBase):
    name = 'Volatility Trend ATR'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.timeperiod_atr = self.strategy_parameters['TIMEPERIOD_ATR']
        self.atr_prev_candles_num = self.strategy_parameters['ATR_PREV_CANDLES_NUM']
        self.main_order_map = None
        self.previous_trend = None
        self.current_trend = None
        self.action_constants = {1: 'BUY', -1: 'SELL'}

    def initialize(self):
        self.main_order_map = {}
        self.previous_trend = {}
        self.current_trend = {}

    @staticmethod
    def get_historical_data_duration():
        return 300

    def get_trend_direction(self, instrument):
        hist_data = self.get_historical_data(instrument)
        atr = talib.ATR(hist_data['high'], hist_data['low'], hist_data['close'], timeperiod=self.timeperiod_atr)
        current_atr = atr.iloc[-1]
        atr_prev_candles_num = atr.iloc[-self.atr_prev_candles_num]
        return 1 if current_atr > atr_prev_candles_num else -1 if current_atr < atr_prev_candles_num else 0

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        instruments, meta = [], []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is None:
                if self.current_trend.get(instrument) in [None, 0]:
                    current_trend = self.current_trend[instrument] = self.get_trend_direction(instrument)
                else:
                    current_trend = self.current_trend[instrument]

                if current_trend in [1, -1]:
                    instruments.append(instrument)
                    meta.append({'action': self.action_constants[current_trend]})
                    self.previous_trend[instrument] = self.current_trend[instrument]

        return instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        instruments, meta = [], []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is not None:
                current_trend = self.current_trend[instrument] = self.get_trend_direction(instrument)
                self.current_trend[instrument] = current_trend
                if current_trend != 0:
                    if current_trend != self.previous_trend.get(instrument):
                        instruments.append(instrument)
                        meta.append({'action': 'EXIT'})

        return instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        if meta['action'] == 'EXIT':
            self.main_order_map[instrument].exit_position()
            self.main_order_map[instrument] = None
            return True

        return False

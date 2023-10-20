import talib

from pyalgotrading.strategy import StrategyBase


class VolatilityTrendATRV2(StrategyBase):
    name = 'Volatility Trend ATR V2'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.timeperiod_atr = self.strategy_parameters['TIMEPERIOD_ATR']
        self.atr_prev_candles_num = self.strategy_parameters['ATR_PREV_CANDLES_NUM']
        self.main_order_map = None
        self.previous_trend = None
        self.current_trend = None

    def initialize(self):
        self.main_order_map = {}
        self.previous_trend = 0
        self.current_trend = 0

    @staticmethod
    def get_historical_data_duration():
        return 300

    def get_trend_direction(self, instrument):
        hist_data = self.get_historical_data(instrument)
        atr = talib.ATR(hist_data['high'], hist_data['low'], hist_data['close'], timeperiod=self.timeperiod_atr)
        current_atr = atr.iloc[-1]
        atr_prev_candles_num = atr.iloc[-self.atr_prev_candles_num]

        if current_atr > atr_prev_candles_num:
            trend = 1
        elif current_atr < atr_prev_candles_num:
            trend = -1
        else:
            trend = 0
        return trend

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        instruments, meta = [], []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is None:
                if self.current_trend == 0:
                    self.current_trend = self.get_trend_direction(instrument)

                if self.current_trend == 1:
                    instruments.append(instrument)
                    meta.append({'action': 'BUY'})
                    self.previous_trend = self.current_trend
                elif self.current_trend == -1:
                    instruments.append(instrument)
                    meta.append({'action': 'SELL'})
                    self.previous_trend = self.current_trend

        return instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        instruments, meta = [], []

        for instrument in instruments_bucket:
            trend = self.get_trend_direction(instrument)
            self.current_trend = trend
            if self.main_order_map.get(instrument) is not None and trend != 0:
                if trend != self.previous_trend:
                    instruments.append(instrument)
                    meta.append({'action': 'EXIT'})

        return instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        if meta['action'] == 'EXIT':
            self.main_order_map[instrument].exit_position()
            self.main_order_map[instrument] = None
            return True

        return False

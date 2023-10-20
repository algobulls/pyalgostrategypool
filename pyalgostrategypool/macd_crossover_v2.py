import talib
from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyBase


class MACDCrossoverV2(StrategyBase):
    name = 'MACD Crossover V2'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.timeperiod_fast = self.strategy_parameters['TIMEPERIOD_FAST']
        self.timeperiod_slow = self.strategy_parameters['TIMEPERIOD_SLOW']
        self.timeperiod_signal = self.strategy_parameters['TIMEPERIOD_SIGNAL']
        self.main_order_map = None

    def initialize(self):
        self.main_order_map = {}

    @staticmethod
    def get_historical_data_duration():
        return 40

    def get_crossover(self, instrument):
        hist_data = self.get_historical_data(instrument)
        macdline, macdsignal, _ = talib.MACD(hist_data['close'], fastperiod=self.timeperiod_fast, slowperiod=self.timeperiod_slow, signalperiod=self.timeperiod_signal)
        crossover_value = self.utils.crossover(macdline, macdsignal)
        return crossover_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        instruments, meta = [], []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is None:
                crossover = self.get_crossover(instrument)
                if crossover == 1:
                    instruments.append(instrument)
                    meta.append({'action': 'BUY'})
                elif crossover == -1:
                    instruments.append(instrument)
                    meta.append({'action': 'SELL'})

        return instruments, meta

    def strategy_enter_position(self, candle, instrument, sideband_info):
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(instrument, sideband_info['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        instruments, meta = [], []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is not None:
                crossover = self.get_crossover(instrument)
                if crossover in [1, -1]:
                    instruments.append(instrument)
                    meta.append({'action': 'EXIT'})

        return instruments, meta

    def strategy_exit_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'EXIT':
            self.main_order_map[instrument].exit_position()
            self.main_order_map[instrument] = None
            return True

        return False

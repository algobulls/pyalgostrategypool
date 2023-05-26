import talib

from pyalgotrading.constants import *
from pyalgotrading.strategy.strategy_base import StrategyBase


class StrategyInverseEMAScalpingRegularOrder(StrategyBase):
    name = 'Inverse EMA Scalping Regular Order Strategy v2'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.larger_time_period = self.strategy_parameters['LARGER_TIME_PERIOD']
        self.smaller_time_period = self.strategy_parameters['SMALLER_TIME_PERIOD']

        assert (0 < self.larger_time_period == int(self.larger_time_period)), f"Strategy parameter LARGER_TIME_PERIOD should be a positive integer. Received: {self.larger_time_period}"
        assert (0 < self.smaller_time_period == int(self.smaller_time_period)), f"Strategy parameter SMALLER_TIME_PERIOD should be a positive integer. Received: {self.smaller_time_period}"

        self.main_order_map = None

    def initialize(self):
        self.main_order_map = {}

    def get_crossover_value(self, instrument):
        hist_data = self.get_historical_data(instrument)
        larger_ema = talib.EMA(hist_data['close'], timeperiod=self.larger_time_period)
        smaller_ema = talib.EMA(hist_data['close'], timeperiod=self.smaller_time_period)
        crossover_value = self.utils.crossover(smaller_ema, larger_ema)
        return crossover_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):

        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:
            crossover_value = self.get_crossover_value(instrument)
            if crossover_value == -1:
                selected_instruments.append(instrument)
                meta.append({'action': 'BUY'})
            elif crossover_value == 1:
                if self.strategy_mode is StrategyMode.INTRADAY:
                    selected_instruments.append(instrument)
                    meta.append({'action': 'SELL'})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, sideband_info):
        # Place buy order
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(instrument, sideband_info['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is not None:
                crossover_value = self.get_crossover_value(instrument)
                if crossover_value in [1, -1]:
                    selected_instruments.append(instrument)
                    meta.append({'action': 'EXIT'})
        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'EXIT':
            self.main_order_map[instrument].exit_position()
            self.main_order_map[instrument] = None
            return True
        return False

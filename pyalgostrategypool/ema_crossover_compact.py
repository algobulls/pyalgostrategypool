import talib

from pyalgotrading.constants import *
from pyalgotrading.strategy.strategy_base import StrategyBase


class StrategyEMARegularOrder(StrategyBase):
    name = 'EMA Regular Order Strategy'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.timeperiod1 = self.strategy_parameters['TIMEPERIOD1']
        self.timeperiod2 = self.strategy_parameters['TIMEPERIOD2']
        self.main_order_map = None

    def initialize(self):
        self.main_order_map = {}

    def get_crossover(self, instrument):
        hist_data = self.get_historical_data(instrument)
        ema_x = talib.EMA(hist_data['close'], timeperiod=self.timeperiod1)
        ema_y = talib.EMA(hist_data['close'], timeperiod=self.timeperiod2)
        return self.utils.crossover(ema_x, ema_y)

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        instruments = []
        meta = []

        for instrument in instruments_bucket:
            crossover = self.get_crossover(instrument)
            if crossover == 1:
                instruments.append(instrument)
                meta.append({'action': 'BUY'})
            elif crossover == -1:
                if self.strategy_mode is StrategyMode.INTRADAY:
                    instruments.append(instrument)
                    meta.append({'action': 'SELL'})

        return instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(meta['action'], BrokerOrderCodeConstants.INTRADAY, BrokerOrderVarietyConstants.MARKET, self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        instruments = []
        meta = []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is not None:
                crossover = self.get_crossover(instrument)
                if crossover in [1, -1]:
                    instruments.append(instrument)
                    meta.append({'action': 'EXIT'})
                    
        return instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        if meta['action'] == 'EXIT':
            self.main_order_map[instrument].exit_position()
            self.main_order_map[instrument] = None
            return True
            
        return False

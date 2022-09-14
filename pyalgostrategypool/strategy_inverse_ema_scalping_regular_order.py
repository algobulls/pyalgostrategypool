import talib

from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyBase


class StrategyInverseEMAScalpingRegularOrder(StrategyBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.larger_time_period = self.strategy_parameters['LARGER_TIME_PERIOD']
        self.smaller_time_period = self.strategy_parameters['SMALLER_TIME_PERIOD']

        assert (0 < self.larger_time_period == int(self.larger_time_period)), f"Strategy parameter LARGER_TIME_PERIOD should be a positive integer. Received: {self.larger_time_period}"
        assert (0 < self.smaller_time_period == int(self.smaller_time_period)), f"Strategy parameter SMALLER_TIME_PERIOD should be a positive integer. Received: {self.smaller_time_period}"

        self.main_order = None

    def initialize(self):
        self.main_order = {}

    @staticmethod
    def name():
        return 'Inverse EMA Scalping Regular Order Strategy'

    @staticmethod
    def versions_supported():
        return [AlgoBullsEngineVersion.VERSION_3_3_0]

    def get_crossover_value(self, instrument):
        hist_data = self.get_historical_data(instrument)
        larger_ema = talib.EMA(hist_data['close'], timeperiod=self.larger_time_period)
        smaller_ema = talib.EMA(hist_data['close'], timeperiod=self.smaller_time_period)
        crossover_value = self.utils.crossover(smaller_ema, larger_ema)
        return crossover_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):

        selected_instruments_bucket = []
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            crossover_value = self.get_crossover_value(instrument)
            if crossover_value == -1:
                selected_instruments_bucket.append(instrument)
                sideband_info_bucket.append({'action': 'BUY'})
            elif crossover_value == 1:
                if self.strategy_mode is StrategyMode.INTRADAY:
                    selected_instruments_bucket.append(instrument)
                    sideband_info_bucket.append({'action': 'SELL'})

        return selected_instruments_bucket, sideband_info_bucket

    def strategy_enter_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'BUY':
            qty = self.number_of_lots * instrument.lot_size
            self.main_order[instrument] = \
                self.broker.BuyOrderRegular(instrument=instrument,
                                            order_code=BrokerOrderCodeConstants.INTRADAY,
                                            order_variety=BrokerOrderVarietyConstants.MARKET,
                                            quantity=qty)
        elif sideband_info['action'] == 'SELL':
            qty = self.number_of_lots * instrument.lot_size
            self.main_order[instrument] = \
                self.broker.SellOrderRegular(instrument=instrument,
                                             order_code=BrokerOrderCodeConstants.INTRADAY,
                                             order_variety=BrokerOrderVarietyConstants.MARKET,
                                             quantity=qty)
        else:
            raise SystemExit(f'Got invalid sideband_info value: {sideband_info}')

        return self.main_order[instrument]

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments_bucket = []
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            if self.main_order.get(instrument) is not None:
                crossover_value = self.get_crossover_value(instrument)
                if crossover_value in [1, -1]:
                    selected_instruments_bucket.append(instrument)
                    sideband_info_bucket.append({'action': 'EXIT'})
        return selected_instruments_bucket, sideband_info_bucket

    def strategy_exit_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'EXIT':
            self.main_order[instrument].exit_position()
            self.main_order[instrument] = None
            return True
        return False

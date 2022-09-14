import talib

from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyBase


class StrategySMABracketOrder(StrategyBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.timeperiod1 = self.strategy_parameters['TIMEPERIOD1']
        self.timeperiod2 = self.strategy_parameters['TIMEPERIOD2']
        self.stoploss = self.strategy_parameters['STOPLOSS_TRIGGER']
        self.target = self.strategy_parameters['TARGET_TRIGGER']
        self.trailing_stoploss = self.strategy_parameters['TRAILING_STOPLOSS_TRIGGER']

        assert (0 < self.timeperiod1 == int(self.timeperiod1)), f"Strategy parameter TIMEPERIOD1 should be a positive integer. Received: {self.timeperiod1}"
        assert (0 < self.timeperiod2 == int(self.timeperiod2)), f"Strategy parameter TIMEPERIOD2 should be a positive integer. Received: {self.timeperiod2}"
        assert (0 < self.stoploss < 1), f"Strategy parameter STOPLOSS_TRIGGER should be a positive fraction between 0 and 1. Received: {self.stoploss}"
        assert (0 < self.target < 1), f"Strategy parameter TARGET_TRIGGER should be a positive fraction between 0 and 1. Received: {self.target}"
        assert (0 < self.trailing_stoploss), f"Strategy parameter TRAILING_STOPLOSS_TRIGGER should be a positive number. Received: {self.trailing_stoploss}"

        self.main_order = None

    def initialize(self):
        self.main_order = {}

    @staticmethod
    def name():
        return 'SMA Bracket Order Strategy'

    @staticmethod
    def versions_supported():
        return [AlgoBullsEngineVersion.VERSION_3_3_0]

    def get_crossover_value(self, instrument):

        hist_data = self.get_historical_data(instrument)
        ema_one = talib.SMA(hist_data['close'], timeperiod=self.timeperiod1)
        ema_two = talib.SMA(hist_data['close'], timeperiod=self.timeperiod2)
        crossover_value = self.utils.crossover(ema_one, ema_two)
        return crossover_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):

        selected_instruments_bucket = []
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            crossover_value = self.get_crossover_value(instrument)
            if crossover_value == 1:
                selected_instruments_bucket.append(instrument)
                sideband_info_bucket.append({'action': 'BUY'})
            elif crossover_value == -1:
                if self.strategy_mode is StrategyMode.INTRADAY:
                    selected_instruments_bucket.append(instrument)
                    sideband_info_bucket.append({'action': 'SELL'})

        return selected_instruments_bucket, sideband_info_bucket

    def strategy_enter_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'BUY':
            qty = self.number_of_lots * instrument.lot_size
            ltp = self.broker.get_ltp(instrument)
            self.main_order[instrument] = \
                self.broker.BuyOrderBracket(instrument=instrument,
                                            order_code=BrokerOrderCodeConstants.INTRADAY,
                                            order_variety=BrokerOrderVarietyConstants.LIMIT,
                                            quantity=qty,
                                            price=ltp,
                                            stoploss_trigger=ltp - (ltp * self.stoploss),
                                            target_trigger=ltp + (ltp * self.target),
                                            trailing_stoploss_trigger=ltp * self.trailing_stoploss)

        elif sideband_info['action'] == 'SELL':
            qty = self.number_of_lots * instrument.lot_size
            ltp = self.broker.get_ltp(instrument)
            self.main_order[instrument] = \
                self.broker.SellOrderBracket(instrument=instrument,
                                             order_code=BrokerOrderCodeConstants.INTRADAY,
                                             order_variety=BrokerOrderVarietyConstants.LIMIT,
                                             quantity=qty,
                                             price=ltp,
                                             stoploss_trigger=ltp + (ltp * self.stoploss),
                                             target_trigger=ltp - (ltp * self.target),
                                             trailing_stoploss_trigger=ltp * self.trailing_stoploss)
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

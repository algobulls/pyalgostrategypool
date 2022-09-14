import talib

from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyBase


class StrategyAroonCrossoverCoverOrder(StrategyBase):
    class ActionConstants:
        NO_ACTION = 0
        ENTRY_BUY_OR_EXIT_SELL = 1
        ENTRY_SELL_OR_EXIT_BUY = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.time_period = self.strategy_parameters['TIME_PERIOD']
        self.stoploss = self.strategy_parameters['STOPLOSS_TRIGGER']

        assert (0 < self.time_period == int(self.time_period)), f"Strategy parameter TIME_PERIOD should be a positive integer. Received: {self.time_period}"
        assert (0 < self.stoploss < 1), f"Strategy parameter STOPLOSS_TRIGGER should be a positive fraction between 0 and 1. Received: {self.stoploss}"

        self.main_order = None

    def initialize(self):
        self.main_order = {}

    @staticmethod
    def name():
        return 'Aroon Crossover Cover Order Strategy'

    @staticmethod
    def versions_supported():
        return [AlgoBullsEngineVersion.VERSION_3_3_0]

    def get_decision(self, instrument):
        hist_data = self.get_historical_data(instrument)
        aroondown, aroonup = talib.AROON(hist_data['high'], hist_data['low'], timeperiod=self.time_period)
        aroon_crossover_value = self.utils.crossover(aroonup, aroondown)
        if aroon_crossover_value == 1:
            action = self.ActionConstants.ENTRY_BUY_OR_EXIT_SELL
        elif aroon_crossover_value == -1:
            action = self.ActionConstants.ENTRY_SELL_OR_EXIT_BUY
        else:
            action = self.ActionConstants.NO_ACTION
        return action

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):

        selected_instruments_bucket = []
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            action = self.get_decision(instrument)
            if self.main_order.get(instrument) is None:
                if action is self.ActionConstants.ENTRY_BUY_OR_EXIT_SELL:
                    selected_instruments_bucket.append(instrument)
                    sideband_info_bucket.append({'action': 'BUY'})
                elif action is self.ActionConstants.ENTRY_SELL_OR_EXIT_BUY:
                    if self.strategy_mode is StrategyMode.INTRADAY:
                        selected_instruments_bucket.append(instrument)
                        sideband_info_bucket.append({'action': 'SELL'})

        return selected_instruments_bucket, sideband_info_bucket

    def strategy_enter_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'BUY':
            qty = self.number_of_lots * instrument.lot_size
            ltp = self.broker.get_ltp(instrument)
            self.main_order[instrument] = self.broker.BuyOrderCover(instrument=instrument,
                                                                    order_code=BrokerOrderCodeConstants.INTRADAY,
                                                                    order_variety=BrokerOrderVarietyConstants.MARKET,
                                                                    quantity=qty,
                                                                    price=ltp,
                                                                    trigger_price=ltp - (ltp * self.stoploss))
        elif sideband_info['action'] == 'SELL':
            qty = self.number_of_lots * instrument.lot_size
            ltp = self.broker.get_ltp(instrument)
            self.main_order[instrument] = self.broker.SellOrderCover(instrument=instrument,
                                                                     order_code=BrokerOrderCodeConstants.INTRADAY,
                                                                     order_variety=BrokerOrderVarietyConstants.MARKET,
                                                                     quantity=qty,
                                                                     price=ltp,
                                                                     trigger_price=ltp + (ltp * self.stoploss))
        else:
            raise SystemExit(f'Invalid sideband info value {sideband_info}')

        return self.main_order[instrument]

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments_bucket = []
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            if self.main_order.get(instrument) is not None:
                action = self.get_decision(instrument)
                if ((self.main_order[instrument].order_transaction_type is BrokerOrderTransactionTypeConstants.BUY and
                     action is self.ActionConstants.ENTRY_SELL_OR_EXIT_BUY) or
                        (self.main_order[instrument].order_transaction_type is BrokerOrderTransactionTypeConstants.SELL and
                         action is self.ActionConstants.ENTRY_BUY_OR_EXIT_SELL)):
                    selected_instruments_bucket.append(instrument)
                    sideband_info_bucket.append({'action': 'EXIT'})
        return selected_instruments_bucket, sideband_info_bucket

    def strategy_exit_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'EXIT':
            self.main_order[instrument].exit_position()
            self.main_order[instrument] = None
            return True
        return False

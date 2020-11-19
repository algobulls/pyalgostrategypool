import talib

from constants import *
from strategy.core.strategy_base import StrategyBase


class StrategyWilliamsPercentageCustomBracketOrder(StrategyBase):
    class MktAction(Enum):
        NO_ACTION = 0
        ENTRY_BUY_EXIT_SELL = 1
        ENTRY_SELL_EXIT_BUY = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.time_period = self.strategy_parameters['TIME_PERIOD']
        self.std_deviations = self.strategy_parameters['STANDARD_DEVIATIONS']

        self.stoploss = self.strategy_parameters['STOPLOSS_TRIGGER']
        self.target = self.strategy_parameters['TARGET_TRIGGER']
        self.trailing_stoploss = self.strategy_parameters['TRAILING_STOPLOSS_TRIGGER']

        assert (0 < self.time_period == int(self.time_period)), f"Strategy parameter TIME_PERIOD should be a positive integer. Received: {self.time_period}"
        assert (0 < self.std_deviations == int(self.std_deviations)), f"Strategy parameter STANDARD_DEVIATIONS should be a positive integer. Received: {self.std_deviations}"

        assert (0 < self.stoploss < 1), f"Strategy parameter STOPLOSS_TRIGGER should be a positive fraction between 0 and 1. Received: {self.stoploss}"
        assert (0 < self.target < 1), f"Strategy parameter TARGET_TRIGGER should be a positive fraction between 0 and 1. Received: {self.target}"
        assert (0 < self.trailing_stoploss), f"Strategy parameter TRAILING_STOPLOSS_TRIGGER should be a positive number. Received: {self.trailing_stoploss}"

        self.main_order = None

    def initialize(self):
        self.main_order = {}

    @staticmethod
    def name():
        return 'Williams Percentage Custom Bracket Order Strategy'

    @staticmethod
    def versions_supported():
        return AlgoBullsEngineVersion.VERSION_3_2_0

    def get_market_action(self, instrument):
        hist_data = self.get_historical_data(instrument)
        action = self.MktAction.NO_ACTION
        return action

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):

        selected_instruments_bucket = []
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            action = self.get_market_action(instrument)
            if self.main_order.get(instrument) is None:
                if action is self.MktAction.ENTRY_BUY_EXIT_SELL:
                    selected_instruments_bucket.append(instrument)
                    sideband_info_bucket.append({'action': 'BUY'})
                elif action is self.MktAction.ENTRY_SELL_EXIT_BUY:
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
                action = self.get_market_action(instrument)
                if (self.main_order[instrument].order_transaction_type is BrokerOrderTransactionTypeConstants.BUY and action is self.MktAction.ENTRY_SELL_EXIT_BUY) or \
                        (self.main_order[instrument].order_transaction_type is BrokerOrderTransactionTypeConstants.SELL and action is self.MktAction.ENTRY_BUY_EXIT_SELL):
                    selected_instruments_bucket.append(instrument)
                    sideband_info_bucket.append({'action': 'EXIT'})
        return selected_instruments_bucket, sideband_info_bucket

    def strategy_exit_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'EXIT':
            self.main_order[instrument].exit_position()
            self.main_order[instrument] = None
            return True

        return False

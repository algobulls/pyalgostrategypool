from datetime import datetime

import clock
from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyBase



class OpenRangeBreakoutCoverOrder(StrategyBase):
    class ActionConstants:
        NO_ACTION = 0
        ENTRY_BUY_OR_EXIT_SELL = 1
        ENTRY_SELL_OR_EXIT_BUY = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.user_defined_candle_start_time_hours = self.strategy_parameters['USER_DEFINED_CANDLE_START_TIME_HOURS']
        self.user_defined_candle_start_time_minutes = self.strategy_parameters['USER_DEFINED_CANDLE_START_TIME_MINUTES']

        self.stoploss = self.strategy_parameters['STOPLOSS_TRIGGER']

        try:
            str_time = str(self.user_defined_candle_start_time_hours) + ':' + str(self.user_defined_candle_start_time_minutes)
            self.udc_candle = (datetime.strptime(str_time, '%H:%M'))
            self.udc_candle = self.udc_candle.time()
        except Exception as e:
            self.logger.fatal('Error in conversion of Strategy parameters into USER_DEFINED_CANDLE_START_TIME_HOURS and'
                              ' USER_DEFINED_CANDLE_START_TIME_MINUTES into proper time format... EXITING')
            raise SystemExit

        assert (0 < self.stoploss < 1), f"Strategy parameter STOPLOSS_TRIGGER should be a positive fraction between 0 and 1. Received: {self.stoploss}"

        self.main_order = None
        self.udc_high = None
        self.order_placed_for_the_day = None

    def initialize(self):
        self.main_order = {}
        self.udc_high = {}
        self.order_placed_for_the_day = {}

    @staticmethod
    def name():
        return 'Open Range Breakout Cover Order Strategy'

    @staticmethod
    def versions_supported():
        return [AlgoBullsEngineVersion.VERSION_3_3_0]

    def get_decision(self, instrument):
        hist_data = self.get_historical_data(instrument)
        timestamp_str = str(hist_data['timestamp'].iloc[-1].to_pydatetime().time())
        udc_candle_str = str(self.udc_candle)
        if timestamp_str == udc_candle_str:
            self.udc_high[instrument] = hist_data['high'].iloc[-1]
        action = self.ActionConstants.NO_ACTION
        if self.udc_high.get(instrument) is not None:
            if self.ohlc_crossovers_with_value(hist_data, self.udc_high[instrument], 1):
                action = self.ActionConstants.ENTRY_BUY_OR_EXIT_SELL
            elif self.ohlc_crossovers_with_value(hist_data, self.udc_high[instrument], -1):
                action = self.ActionConstants.ENTRY_SELL_OR_EXIT_BUY
            else:
                action = self.ActionConstants.NO_ACTION
        return action

    def ohlc_crossovers_with_value(self, hist_data, val, expected):
        truth_value = False
        columns = ['open', 'high', 'low', 'close']
        val_data = [val] * len(hist_data)
        for column in columns:
            crossover = self.utils.crossover(hist_data[column], val_data, self.crossover_accuracy_decimals)
            if crossover == expected:
                truth_value = True
                break
        return truth_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):

        selected_instruments_bucket = []
        sideband_info_bucket = []
        if not (clock.CLOCK.now().time() >= self.udc_candle):
            pass
        else:
            for instrument in instruments_bucket:
                if self.order_placed_for_the_day.get(instrument) is None:
                    action = self.get_decision(instrument)
                    if self.main_order.get(instrument) is None:
                        if action is self.ActionConstants.ENTRY_BUY_OR_EXIT_SELL:
                            selected_instruments_bucket.append(instrument)
                            sideband_info_bucket.append({'action': 'BUY'})
                        elif action is self.ActionConstants.ENTRY_SELL_OR_EXIT_BUY:
                            if self.strategy_mode is StrategyMode.INTRADAY:
                                selected_instruments_bucket.append(instrument)
                                sideband_info_bucket.append({'action': 'SELL'})
                elif self.order_placed_for_the_day.get(instrument) is True:
                    self.logger.info('ORDER PLACED FOR THE DAY, NO MORE ORDERS WILL BE PLACED FOR THE REMAINING DAY')
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
            self.order_placed_for_the_day[instrument] = True
        elif sideband_info['action'] == 'SELL':
            qty = self.number_of_lots * instrument.lot_size
            ltp = self.broker.get_ltp(instrument)
            self.main_order[instrument] = self.broker.SellOrderCover(instrument=instrument,
                                                                     order_code=BrokerOrderCodeConstants.INTRADAY,
                                                                     order_variety=BrokerOrderVarietyConstants.MARKET,
                                                                     quantity=qty,
                                                                     price=ltp,
                                                                     trigger_price=ltp + (ltp * self.stoploss))
            self.order_placed_for_the_day[instrument] = True
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

import talib

from pyalgotrading.constants import *
from pyalgotrading.strategy.strategy_base import StrategyBase


class EMABracket(StrategyBase):

    def __init__(self, *args, **kwargs):
        """
        Accept and sanitize all your parameters here.
        Setup the variables you will need here.
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        """

        super().__init__(*args, **kwargs)

        # EMA parameters
        self.timeperiod1 = self.strategy_parameters['TIMEPERIOD1']
        self.timeperiod2 = self.strategy_parameters['TIMEPERIOD2']
        self.stoploss = self.strategy_parameters['STOPLOSS_TRIGGER']
        self.target = self.strategy_parameters['TARGET_TRIGGER']
        self.trailing_stoploss = self.strategy_parameters['TRAILING_STOPLOSS_TRIGGER']

        # Sanity
        assert (0 < self.timeperiod1 == int(self.timeperiod1)), f"Strategy parameter TIMEPERIOD1 should be a positive integer. Received: {self.timeperiod1}"
        assert (0 < self.timeperiod2 == int(self.timeperiod2)), f"Strategy parameter TIMEPERIOD2 should be a positive integer. Received: {self.timeperiod2}"
        assert (0 < self.stoploss < 1), f"Strategy parameter STOPLOSS_TRIGGER should be a positive fraction between 0 and 1. Received: {self.stoploss}"
        assert (0 < self.target < 1), f"Strategy parameter TARGET_TRIGGER should be a positive fraction between 0 and 1. Received: {self.target}"
        assert (0 < self.trailing_stoploss), f"Strategy parameter TRAILING_STOPLOSS_TRIGGER should be a positive number. Received: {self.trailing_stoploss}"

        # Variables
        self.main_order = None

    def initialize(self):
        """
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        Use this to initialize and re-initialize your variables.
        """

        self.main_order = {}

    @staticmethod
    def name():
        """
        Name of your strategy.
        """

        return 'EMA Bracket'

    @staticmethod
    def versions_supported():
        """
        Strategy should always support the latest engine version.
        Current version is 3.3.0
        """

        return AlgoBullsEngineVersion.VERSION_3_3_0

    def get_decision(self, instrument, decision):
        """
        This method calculates the crossover using the hist data of the instrument along with the required indicator and returns the entry/exit action.
        """

        # Get OHLC historical data for the instrument
        hist_data = self.get_historical_data(instrument)

        # Calculate the EMA values
        ema_x = talib.EMA(hist_data['close'], timeperiod=self.timeperiod1)
        ema_y = talib.EMA(hist_data['close'], timeperiod=self.timeperiod2)

        # Get the crossover value
        crossover_value = self.utils.crossover(ema_x, ema_y)

        # Return action as BUY if crossover is Upwards and decision is Entry, else SELL if decision is EXIT
        if crossover_value == 1:
            action = ActionConstants.ENTRY_BUY if decision is DecisionConstants.ENTRY_POSITION else ActionConstants.EXIT_SELL

        # Return action as SELL if crossover is Downwards and decision is Entry, else BUY if decision is EXIT
        elif crossover_value == -1:
            action = ActionConstants.ENTRY_SELL if decision is DecisionConstants.ENTRY_POSITION else ActionConstants.EXIT_BUY

        # Return action as NO_ACTION if there is no crossover
        else:
            action = ActionConstants.NO_ACTION

        return action

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        """
        This method is called once every candle time.
        If you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on).
        In a candle, the exit method is called first, then the entry method is called.
        So once a candle starts, strategy_select_instruments_for_exit gets called first
        and then this method strategy_select_instruments_for_entry gets called.
        """

        # Add instrument in this bucket if you want to place an order for it
        # We decide whether to place an instrument in this bucket or not based on the decision making process given below in the loop
        selected_instruments_bucket = []

        # Add accompanying info in this bucket for that particular instrument in the bucket above
        sideband_info_bucket = []

        # Looping over all instruments given by you in the bucket (we can give multiple instruments in the configuration)
        for instrument in instruments_bucket:

            # Compute various things and get the decision to place an order only if no current order is going on (main order is empty / none)
            if self.main_order.get(instrument) is None:

                # Get entry decision
                action = self.get_decision(instrument, DecisionConstants.ENTRY_POSITION)

                if action is ActionConstants.ENTRY_BUY or (action is ActionConstants.ENTRY_SELL and self.strategy_mode is StrategyMode.INTRADAY):
                    # Add instrument to the bucket
                    selected_instruments_bucket.append(instrument)

                    # Add additional info for the instrument
                    sideband_info_bucket.append({'action': action})

        # Return the buckets to the core engine
        # Engine will now call strategy_enter_position with each instrument and its additional info one by one
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

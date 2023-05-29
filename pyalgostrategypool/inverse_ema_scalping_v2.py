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

            # get crossover value
            crossover = self.get_crossover_value(instrument)
            # define key values for action
            action_constants = {-1: 'BUY', 1: 'SELL'}

            if crossover in [-1, 1]:
                # Add instrument to the bucket
                selected_instruments.append(instrument)
                # Add additional info for the instrument
                meta.append({'action': action_constants[crossover]})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        # Place buy order
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is not None:
                # get crossover value
                crossover_value = self.get_crossover_value(instrument)
                if crossover_value in [1, -1]:
                    # Add instrument to the bucket
                    selected_instruments.append(instrument)
                    # Add additional info for the instrument
                    meta.append({'action': 'EXIT'})

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        if meta['action'] == 'EXIT':
            # Exit the main order
            self.main_order_map[instrument].exit_position()

            # Set it to none so that entry decision can be taken properly
            self.main_order_map[instrument] = None

            # Return true so that the core engine knows that this instrument has exited completely
            return True

        # Return false in all other cases
        return False

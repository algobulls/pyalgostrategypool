import talib

from pyalgotrading.strategy.strategy_base import StrategyBase


class ReverseRSICrossover(StrategyBase):
    name = 'Reverse RSI v2'

    def __init__(self, *args, **kwargs):
        """
        Accept and sanitize all your parameters here.
        Setup the variables you will need here.
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        """

        super().__init__(*args, **kwargs)

        # Reverse RSI parameters
        self.time_period = self.strategy_parameters['TIME_PERIOD']
        self.overbought_value = self.strategy_parameters['OVERBOUGHT_VALUE']
        self.oversold_value = self.strategy_parameters['OVERSOLD_VALUE']

        # Sanity
        assert (0 < self.time_period == int(self.time_period)), f"Strategy parameter TIME_PERIOD should be a positive integer. Received: {self.time_period}"
        assert (0 < self.overbought_value == int(self.overbought_value)), f"Strategy parameter OVERBOUGHT_VALUE should be a positive integer. Received: {self.overbought_value}"
        assert (0 < self.oversold_value == int(self.oversold_value)), f"Strategy parameter OVERSOLD_VALUE should be a positive integer. Received: {self.oversold_value}"

        # Variables
        self.main_order_map = None

    def initialize(self):
        """
        If you are running the strategy for multiple days, then this method will be called only once at the start of every day.
        Use this to initialize and re-initialize your variables.
        """

        self.main_order_map = {}

    def get_decision(self, instrument):
        """
        This method calculates the crossover using the hist data of the instrument along with the required indicator and returns the entry/exit action.
        """

        hist_data = self.get_historical_data(instrument)

        rsi_value = talib.RSI(hist_data['close'], timeperiod=self.time_period)
        oversold_list = [self.oversold_value] * rsi_value.size
        overbought_list = [self.overbought_value] * rsi_value.size

        oversold_crossover_value = self.utils.crossover(rsi_value, oversold_list)
        overbought_crossover_value = self.utils.crossover(rsi_value, overbought_list)

        return oversold_crossover_value, overbought_crossover_value

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
        selected_instruments = []

        # Add accompanying info in this bucket for that particular instrument in the bucket above
        meta = []

        # Looping over all instruments given by you in the bucket (we can give multiple instruments in the configuration)
        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is None:
                oversold_crossover_value, overbought_crossover_value = self.get_decision(instrument)
                if oversold_crossover_value == 1:
                    selected_instruments.append(instrument)
                    meta.append({'action': 'BUY'})
                elif overbought_crossover_value == -1:
                    selected_instruments.append(instrument)
                    meta.append({'action': 'SELL'})
        # Return the buckets to the core engine
        # Engine will now call strategy_enter_position with each instrument and its additional info one by one
        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle.
        Place an order here and return it to the core.
        """
        # place order
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        """
        This method is called once every candle time.
        If you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on).
        In a candle, the exit method is called first, then the entry method is called.
        So once a candle starts, this method strategy_select_instruments_for_exit gets called first
        and then strategy_select_instruments_for_entry gets called.
        """

        # Add instrument in this bucket if you want to place an (exit) order for it
        # We decide whether to place an instrument in this bucket or not based on the decision making process given below in the loop
        instruments, meta = [], []

        for instrument in instruments_bucket:
            if self.main_order_map.get(instrument) is not None:
                oversold_crossover_value, overbought_crossover_value = self.get_decision(instrument)
                if (oversold_crossover_value == 1 or overbought_crossover_value == 1) and self.main_order_map[instrument].order_transaction_type.value == 'SELL':
                    instruments.append(instrument)
                    meta.append({'action': 'EXIT'})
                elif (oversold_crossover_value == -1 or overbought_crossover_value == -1) and self.main_order_map[instrument].order_transaction_type.value == 'BUY':
                    instruments.append(instrument)
                    meta.append({'action': 'EXIT'})

        return instruments, meta

    def strategy_exit_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle.
        Exit an order here and return the instrument status to the core.
        """

        if sideband_info['action'] in [ActionConstants.EXIT_BUY, ActionConstants.EXIT_SELL]:
            # Exit the main order
            self.main_order_map[instrument].exit_position()

            # Set it to none so that entry decision can be taken properly
            self.main_order_map[instrument] = None

            # Return true so that the core engine knows that this instrument has exited completely
            return True

        # Return false in all other cases
        return False

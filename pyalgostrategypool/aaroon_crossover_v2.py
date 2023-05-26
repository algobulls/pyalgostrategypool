import talib
from constants import *
from strategy.core.strategy_base import StrategyBase


class AroonCrossoverV2(StrategyBase):
    name = 'Aroon Crossover v2'
    
    def __init__(self, *args, **kwargs):
        """
        Accept and sanitize all your parameters here.
        Set up the variables you will need here.
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        """

        super().__init__(*args, **kwargs)

        # Aroon parameter
        self.time_period = self.strategy_parameters['TIME_PERIOD']

        # Sanity
        assert (0 < self.time_period == int(self.time_period)), f"Strategy parameter TIME_PERIOD should be a positive integer. Received: {self.time_period}"

        # Variables
        self.main_order_map = None

    def initialize(self):
        """
        If you are running the strategy for multiple days, then this method will be called only once at the start of every day.
        Use this to initialize and re-initialize your variables.
        """
        self.logger.info(f"delete this : {self.strategy_parameters}")
        self.main_order_map = {}

    def get_crossover(self, instrument):
        """
        This method calculates the crossover using the hist data of the instrument along with the required indicator and returns the entry/exit action.
        """

        # Get OHLC historical data for the instrument
        hist_data = self.get_historical_data(instrument)

        # Calculate the Stochastic values
        aroon_down, aroon_up = talib.AROON(hist_data['high'], hist_data['low'], timeperiod=self.time_period)

        # Get the crossover value
        crossover_value = self.utils.crossover(aroon_up, aroon_down)
        
        return crossover_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        """
        This method is called once every candle time.
        If you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on).
        In a candle, the exit method is called first, then the entry method is called.
        So once a candle starts, strategy_select_instruments_for_exit gets called first
        and then this method strategy_select_instruments_for_entry gets called.
        """

        # Add instrument in this bucket if you want to place an order for it,
        # We decide whether to place an instrument in this bucket or not based on the decision-making process given below in the loop
        selected_instruments = []

        # Add accompanying info in this bucket for that particular instrument in the bucket above
        meta = []

        # Looping over all instruments given by you in the bucket (we can give multiple instruments in the configuration)
        for instrument in instruments_bucket:

            # Compute various things and get the decision to place an order only if no current order is going on (main order is empty / none)
            if self.main_order_map.get(instrument) is None:

                # Get entry decision
                crossover = self.get_crossover(instrument)
                
                # define key values for action
                action_constants = {1: 'BUY', -1: 'SELL'}
                
                if crossover in [-1, 1]:
                    # Add instrument to the bucket
                    selected_instruments.append(instrument)
                    
                    # Add additional info for the instrument
                    meta.append({'action': action_constants[crossover]})

        # Return the buckets to the core engine.
        # Engine will now call strategy_enter_position with each instrument and its additional info one by one
        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle.
        Place an order here and return it to the core.
        """

        # Place buy order
        self.main_order_map[instrument] = _ = self.broker.OrderRegular(instrument, sideband_info['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _        

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        """
        This method is called once every candle time.
        If you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on).
        In a candle, the exit method is called first, then the entry method is called.
        So once a candle starts, this method strategy_select_instruments_for_exit gets called first
        and then strategy_select_instruments_for_entry gets called.
        """

        # Add instrument in this bucket if you want to place an (exit) order for it,
        # We decide whether to place an instrument in this bucket or not based on the decision-making process given below in the loop
        selected_instruments = []

        # Add accompanying info in this bucket for that particular instrument in the bucket above
        meta = []

        for instrument in instruments_bucket:
            main_order = self.main_order_map.get(instrument)

            # Compute various things and get the decision to place an (exit) order only if there is a current order is going on (main order is not empty / none)
            # Also check if order status is complete
            if main_order is not None and main_order.get_order_status() is BrokerOrderStatusConstants.COMPLETE:

                # Check for action (decision-making process)
                crossover = self.get_crossover(instrument)

                if (crossover == 1 and self.main_order_map[instrument].order_transaction_type.value == 'SELL') or (crossover == -1 and self.main_order_map[instrument].order_transaction_type.value == 'BUY'):
                    selected_instruments.append(instrument)
                    meta.append({"action": 'EXIT'})

        # Return the buckets to the core engine.
        # Engine will now call strategy_exit_position with each instrument and its additional info one by one.
        return selected_instruments, meta

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

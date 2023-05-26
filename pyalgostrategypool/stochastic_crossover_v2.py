import talib

from pyalgotrading.constants import *
from pyalgotrading.strategy.strategy_base import StrategyBase


class StochasticCrossover(StrategyBase):
    name = 'Stochastic Crossover v2'

    def __init__(self, *args, **kwargs):
        """
        Accept and sanitize all your parameters here.
        Setup the variables you will need here.
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        """
        super().__init__(*args, **kwargs)

        # Stochastic parameters
        self.fastk_period = self.strategy_parameters.get('FASTK_PERIOD') or self.strategy_parameters.get('PERIOD')
        self.slowk_period = self.strategy_parameters.get('SLOWK_PERIOD') or self.strategy_parameters.get('SMOOTH_K_PERIOD')
        self.slowd_period = self.strategy_parameters.get('SLOWD_PERIOD') or self.strategy_parameters.get('SMOOTH_D_PERIOD')

        # Sanity
        assert (0 < self.fastk_period == int(self.fastk_period)), f"Strategy parameter FASTK_PERIOD/PERIOD should be a positive integer. Received: {self.fastk_period}"
        assert (0 < self.slowk_period == int(self.slowk_period)), f"Strategy parameter SLOWK_PERIOD/SMOOTH_K_PERIOD should be a positive integer. Received: {self.slowk_period}"
        assert (0 < self.slowd_period == int(self.slowd_period)), f"Strategy parameter SLOWD_PERIOD/SMOOTH_D_PERIOD should be a positive integer. Received: {self.slowd_period}"

        # Variables
        self.main_order_map = None

    def initialize(self):
        """
        If you are running the strategy for multiple days, then this method will be called only once at the start of every day.
        Use this to initialize and re-initialize your variables.
        """
        self.main_order_map = {}

    @staticmethod
    def versions_supported():
        """
        Strategy should always support the latest engine version.
        Current version is 3.3.0
        """
        return AlgoBullsEngineVersion.VERSION_3_3_0

    def get_decision(self, instrument, decision):
        """
        This method calculates the crossover using the hist data of the instrument along with the required indicator.
        """

        # Get OHLC historical data for the instrument
        hist_data = self.get_historical_data(instrument)

        # Calculate the Stochastic values
        slowk, slowd = talib.STOCH(hist_data['high'], hist_data['low'], hist_data['close'], fastk_period=self.fastk_period,
                                   slowk_period=self.slowk_period, slowk_matype=0, slowd_period=self.slowd_period, slowd_matype=0)

        # Get the crossover value
        crossover_value = self.utils.crossover(slowk, slowd)

        # Return crossover value
        return crossover_value

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

            # Compute various things and get the decision to place an order only if no current order is going on (main order is empty / none)
            if self.main_order_map.get(instrument) is None:

                # Get entry decision
                crossover = self.get_decision(instrument, DecisionConstants.ENTRY_POSITION)

                if crossover == 1:
                    # Add instrument to the bucket
                    selected_instruments.append(instrument)
                    # Add additional info for the instrument
                    meta.append({'action': "BUY"})

                elif crossover == -1:
                    # Add instrument to the bucket
                    selected_instruments.append(instrument)
                    # Add additional info for the instrument
                    meta.append({'action': "SELL"})

        # Return the buckets to the core engine
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

        # Add instrument in this bucket if you want to place an (exit) order for it
        # We decide whether to place an instrument in this bucket or not based on the decision making process given below in the loop
        selected_instruments = []

        # Add accompanying info in this bucket for that particular instrument in the bucket above
        meta = []

        for instrument in instruments_bucket:
            main_order = self.main_order_map.get(instrument)

            # Compute various things and get the decision to place an (exit) order only if there is a current order is going on (main order is not empty / none)
            # Also check if order status is complete
            if main_order is not None and main_order.get_order_status() is BrokerOrderStatusConstants.COMPLETE:

                # Check for crossover (decision making process)
                crossover = self.get_decision(instrument, DecisionConstants.EXIT_POSITION)

                # For this strategy, we take the decision as:
                # If order transaction type is buy and current action is sell or order transaction type is sell and current action is buy, then exit the order
                if (crossover == 1 and main_order.order_transaction_type == "SELL") or (crossover == -1 and main_order.order_transaction_type == "BUY"):
                    # Add instrument to the bucket
                    selected_instruments.append(instrument)

                    # Add additional info for the instrument
                    meta.append({'action': "EXIT"})

        # Return the buckets to the core engine
        # Engine will now call strategy_exit_position with each instrument and its additional info one by one
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

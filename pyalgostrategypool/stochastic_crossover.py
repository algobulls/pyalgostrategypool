import logging

import talib
from pyalgotrading.constants import *
from pyalgotrading.strategy.strategy_base import StrategyBase

logger = logging.getLogger('strategy')


class StochasticCrossover(StrategyBase):

    def __init__(self, *args, **kwargs):
        """
        Accept and sanitize all your parameters here
        setup the variables ou will need here
        if you are running the strategy for multiple days, then this method will be called only once at the start of the strategy
        """
        super().__init__(*args, **kwargs)

        # Stochastic parameters
        self.fastk_period = self.strategy_parameters['FASTK_PERIOD']
        self.slowk_period = self.strategy_parameters['SLOWK_PERIOD']
        self.slowd_period = self.strategy_parameters['SLOWD_PERIOD']

        # Sanity
        assert (0 < self.fastk_period == int(self.fastk_period)), f"Strategy parameter FASTK_PERIOD should be a positive integer. Received: {self.fastk_period}"
        assert (0 < self.slowk_period == int(self.slowk_period)), f"Strategy parameter SLOWK_PERIOD should be a positive integer. Received: {self.slowk_period}"
        assert (0 < self.slowd_period == int(self.slowd_period)), f"Strategy parameter SLOWD_PERIOD should be a positive integer. Received: {self.slowd_period}"

        # variables
        self.main_order = None

    def initialize(self):
        """
        If you are running the strategy for multiple days, then this method will be called once at the start of every day
        use this to initialize and re-initialize your variables
        """
        self.main_order = {}

    @staticmethod
    def name():
        """
        Name of your strategy
        """
        return 'Stochastic Crossover'

    @staticmethod
    def versions_supported():
        """
        Strategy should always support the latest engine version
        Current version is 3.3.0
        """
        return AlgoBullsEngineVersion.VERSION_3_3_0

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        """
        This method is called once every candle time
        if you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on)
        in a candle, the exit method is called first, then the entry method is called
        so once a candle starts, strategy_select_instruments_for_exit gets called first
        and then this method - strategy_select_instruments_for_entry gets called
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

                # Check for crossover (decision making process)
                crossover_value = self.get_crossover_value(instrument)

                # For this strategy, we take the decision as:
                # If crossover is upwards place a buy order, if crossover is downwards place a sell order
                if crossover_value == 1:

                    # Add instrument to the bucket
                    selected_instruments_bucket.append(instrument)

                    # Add additional info for the instrument
                    sideband_info_bucket.append({'action': 'BUY'})
                elif crossover_value == -1 and self.strategy_mode is StrategyMode.INTRADAY:

                    # Add instrument to the bucket
                    selected_instruments_bucket.append(instrument)

                    # Add additional info for the instrument
                    sideband_info_bucket.append({'action': 'SELL'})

        # Return the buckets to the core engine
        # Engine will now call strategy_enter_position with each instrument and its additional info one by one
        return selected_instruments_bucket, sideband_info_bucket

    def strategy_enter_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle
        place an order here and return it to the core
        """
        # Quantity formula (number of lots comes from the config)
        qty = self.number_of_lots * instrument.lot_size

        # Place buy order
        if sideband_info['action'] == 'BUY':
            self.main_order[instrument] = self.broker.BuyOrderRegular(instrument=instrument, order_code=BrokerOrderCodeConstants.INTRADAY, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=qty)

        # Place sell order
        elif sideband_info['action'] == 'SELL':
            self.main_order[instrument] = self.broker.SellOrderRegular(instrument=instrument, order_code=BrokerOrderCodeConstants.INTRADAY, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=qty)

        # Sanity
        else:
            raise SystemExit(f'Got invalid sideband_info value: {sideband_info}')

        # Return the order to the core engine for management
        return self.main_order[instrument]

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        """
        This method is called once every candle time
        if you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on)
        in a candle, the exit method is called first, then the entry method is called
        so once a candle starts, this method - strategy_select_instruments_for_exit gets called first
        and then strategy_select_instruments_for_entry gets called
        """

        # Add instrument in this bucket if you want to place an (exit) order for it
        # We decide whether to place an instrument in this bucket or not based on the decision making process given below in the loop
        selected_instruments_bucket = []

        # Add accompanying info in this bucket for that particular instrument in the bucket above
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            main_order = self.main_order.get(instrument)

            # Compute various things and get the decision to place an (exit) order only if there is a current order is going on (main order is not empty / none)
            # Also check if order status is complete
            if main_order is not None and main_order.get_order_status().value == 'COMPLETE':

                # Check for crossover (decision making process)
                crossover_value = self.get_crossover_value(instrument)

                # For this strategy, we take the decision as:
                # If order transaction type is buy and crossover is downwards or order transaction type is sell and crossover is upwards, then exit the order
                if (crossover_value == 1 and main_order.order_transaction_type is BrokerOrderTransactionTypeConstants.SELL) or \
                        (crossover_value == -1 and main_order.order_transaction_type is BrokerOrderTransactionTypeConstants.BUY):

                    # Add instrument to the bucket
                    selected_instruments_bucket.append(instrument)

                    # Add additional info for the instrument
                    sideband_info_bucket.append({'action': 'EXIT'})

        # Return the buckets to the core engine
        # Engine will now call strategy_exit_position with each instrument and its additional info one by one
        return selected_instruments_bucket, sideband_info_bucket

    def strategy_exit_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle
        exit an order here and return the instrument status to the core
        """
        if sideband_info['action'] == 'EXIT':

            # Exit the main order
            self.main_order[instrument].exit_position()

            # Set it to none so that entry decision can be taken properly
            self.main_order[instrument] = None

            # Return true so that the core engine knows that this instrument has exited completely
            return True

        # Return false in all other cases
        return False

    def get_crossover_value(self, instrument):
        """
        This method calculates the crossover using the hist data of the instrument along with the required indicator
        """

        # Get OHLC historical data for the instrument
        hist_data = self.get_historical_data(instrument)

        # Calculate the Stochastic values
        slowk, slowd = talib.STOCH(hist_data['high'], hist_data['low'], hist_data['close'], fastk_period=self.fastk_period,
                                   slowk_period=self.slowk_period, slowk_matype=0, slowd_period=self.slowd_period, slowd_matype=0)

        # Get the crossover value
        stochastic_crossover_value = self.utils.crossover(slowk, slowd)

        # Return the crossover value
        return stochastic_crossover_value

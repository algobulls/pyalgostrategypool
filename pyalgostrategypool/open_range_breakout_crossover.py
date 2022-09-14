from datetime import time

import clock
from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyBase


class OpenRangeBreakoutCrossover(StrategyBase):

    def __init__(self, *args, **kwargs):
        """
        Accept and sanitize all your parameters here.
        Setup the variables you will need here.
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        """

        super().__init__(*args, **kwargs)

        # Open Range Breakout parameters
        self.start_time_hours = self.strategy_parameters['START_TIME_HOURS']
        self.start_time_minutes = self.strategy_parameters['START_TIME_MINUTES']

        # Strategy start time
        try:
            self.candle_start_time = time(hour=self.start_time_hours, minute=self.start_time_minutes)
        except ValueError:
            self.logger.fatal('Error converting hours and minutes... EXITING')
            raise SystemExit

        # Variables
        self.main_order = None
        self.order_placed_for_the_day = None

    def initialize(self):
        """
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        Use this to initialize and re-initialize your variables.
        """

        self.main_order = {}
        self.order_placed_for_the_day = {}

    @staticmethod
    def name():
        """
        Name of your strategy.
        """

        return 'Open Range Breakout Crossover'

    @staticmethod
    def versions_supported():
        """
        Strategy should always support the latest engine version.
        Current version is 3.3.0
        """

        return AlgoBullsEngineVersion.VERSION_3_3_0

    def get_decision(self, instrument, decision):
        """
        This method returns the entry/exit action based on the crossover value
        """
        action = None

        # Get OHLC historical data for the instrument
        hist_data = self.get_historical_data(instrument)

        # Get latest timestamp
        timestamp_str = str(hist_data['timestamp'].iloc[-1].to_pydatetime().time())

        # Get string value of strategy start time
        udc_candle_str = str(self.candle_start_time)

        # If latest timestamp is equal to strategy start time
        if timestamp_str == udc_candle_str:
            latest_high = hist_data['high'].iloc[-1]

            # Return action as BUY if crossover is Upwards and decision is Entry, else SELL if decision is EXIT
            if self.get_crossover_value(hist_data, latest_high) == 1:
                action = ActionConstants.ENTRY_BUY if decision is DecisionConstants.ENTRY_POSITION else ActionConstants.EXIT_SELL

            # Return action as SELL if crossover is Downwards and decision is Entry, else BUY if decision is EXIT
            elif self.get_crossover_value(hist_data, latest_high) == -1:
                action = ActionConstants.ENTRY_SELL if decision is DecisionConstants.ENTRY_POSITION else ActionConstants.EXIT_BUY

            # Return action as NO_ACTION if there is no crossover
            else:
                action = ActionConstants.NO_ACTION
        return action

    def get_crossover_value(self, hist_data, latest_high):
        """
        This method calculates the crossover using the hist data of the instrument along with the required indicator and returns the crossover value.
        """

        crossover = 0

        # Calculate crossover for the OHLC columns with
        columns = ['open', 'high', 'low', 'close']
        val_data = [latest_high] * len(hist_data)
        for column in columns:
            crossover = self.utils.crossover(hist_data[column], val_data)
            if crossover in [1, -1]:

                # If crossover is upwards or downwards, stop computing the crossovers
                break

        # Return the crossover values
        return crossover

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

        # If current time is equal to greater than strategy start time, then take entry decision
        if clock.CLOCK.now().time() >= self.candle_start_time:

            # Looping over all instruments given by you in the bucket (we can give multiple instruments in the configuration)
            for instrument in instruments_bucket:

                # Instrument is appended once the main order is exited, this ensures that only one order is placed for the day
                if instrument not in self.order_placed_for_the_day:

                    # Compute various things and get the decision to place an order only if no current order is going on (main order is empty / none)
                    if self.main_order.get(instrument) is None:

                        # Get entry decision
                        action = self.get_decision(instrument, DecisionConstants.ENTRY_POSITION)

                        if action is ActionConstants.ENTRY_BUY or (action is ActionConstants.ENTRY_SELL and self.strategy_mode is StrategyMode.INTRADAY):
                            # Add instrument to the bucket
                            selected_instruments_bucket.append(instrument)

                            # Add additional info for the instrument
                            sideband_info_bucket.append({'action': action})

                # If one order has exited already, below message is printed
                else:
                    self.logger.info('Order placed for the day, no more orders will be placed for the remaining day')

        # Return the buckets to the core engine
        # Engine will now call strategy_enter_position with each instrument and its additional info one by one
        return selected_instruments_bucket, sideband_info_bucket

    def strategy_enter_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle.
        Place an order here and return it to the core.
        """

        # Quantity formula (number of lots comes from the config)
        qty = self.number_of_lots * instrument.lot_size

        # Place buy order
        if sideband_info['action'] is ActionConstants.ENTRY_BUY:
            self.main_order[instrument] = self.broker.BuyOrderRegular(instrument=instrument, order_code=BrokerOrderCodeConstants.INTRADAY, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=qty)
            self.order_placed_for_the_day[instrument] = True

        # Place sell order
        elif sideband_info['action'] is ActionConstants.ENTRY_SELL:
            self.main_order[instrument] = self.broker.SellOrderRegular(instrument=instrument, order_code=BrokerOrderCodeConstants.INTRADAY, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=qty)
            self.order_placed_for_the_day[instrument] = True

        # Sanity
        else:
            raise SystemExit(f'Got invalid sideband_info value: {sideband_info}')

        # Return the order to the core engine for management
        return self.main_order[instrument]

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
        selected_instruments_bucket = []

        # Add accompanying info in this bucket for that particular instrument in the bucket above
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            main_order = self.main_order.get(instrument)

            # Compute various things and get the decision to place an (exit) order only if there is a current order is going on (main order is not empty / none)
            # Also check if order status is complete
            if main_order is not None and main_order.get_order_status() is BrokerOrderStatusConstants.COMPLETE:

                # Check for action (decision making process)
                action = self.get_decision(instrument, DecisionConstants.EXIT_POSITION)

                # For this strategy, we take the decision as:
                # If order transaction type is buy and current action is sell or order transaction type is sell and current action is buy, then exit the order
                if (action is ActionConstants.EXIT_SELL and main_order.order_transaction_type is BrokerOrderTransactionTypeConstants.SELL) or \
                        (action is ActionConstants.EXIT_BUY and main_order.order_transaction_type is BrokerOrderTransactionTypeConstants.BUY):
                    # Add instrument to the bucket
                    selected_instruments_bucket.append(instrument)

                    # Add additional info for the instrument
                    sideband_info_bucket.append({'action': action})

        # Return the buckets to the core engine
        # Engine will now call strategy_exit_position with each instrument and its additional info one by one
        return selected_instruments_bucket, sideband_info_bucket

    def strategy_exit_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle.
        Exit an order here and return the instrument status to the core.
        """

        if sideband_info['action'] in [ActionConstants.EXIT_BUY, ActionConstants.EXIT_SELL]:
            # Exit the main order
            self.main_order[instrument].exit_position()

            # Set it to none so that entry decision can be taken properly
            self.main_order[instrument] = None

            # Return true so that the core engine knows that this instrument has exited completely
            return True

        # Return false in all other cases
        return False

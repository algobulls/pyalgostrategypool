"""
    Strategy Description:
        This strategy leverages two distinct candlestick patterns for entering options trades based on price movements.
        It checks for directional trends by comparing the latest close price to previous candlestick highs and lows, using these signals to trigger buy or sell options.
        Exit conditions are determined based on target and stop-loss points from the entry price.
"""

from pyalgotrading.strategy import StrategyOptionsBase


class StrategyOptionsBuyingMultiCandleTrendIntraday(StrategyOptionsBase):
    name = 'Strategy Options Buying Multi Candle Trend Intraday'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.no_of_strikes_ce = self.strategy_parameters['NO_OF_STRIKES_CE']  # The number of strikes away from the current price to select for call options (e.g., 1 strike away, 2 strikes away, etc.).
        self.no_of_strikes_pe = self.strategy_parameters['NO_OF_STRIKES_PE']  # The number of strikes away from the current price to select for put options (e.g., 1 strike away, 2 strikes away, etc.).

        self.strike_direction_ce = OptionsStrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_CE']]  # Determines the strike selection for call options based on the configured parameter.
                                                                                                        # It maps 0 to In-The-Money (ITM), 1 to At-The-Money (ATM), and 2 to Out-Of-The-Money (OTM) strikes.

        self.strike_direction_pe = OptionsStrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_PE']]  # Determines the strike selection for put options based on the configured parameter.
                                                                                                        # It maps 0 to In-The-Money (ITM), 1 to At-The-Money (ATM), and 2 to Out-Of-The-Money (OTM) strikes.

        self.target_points = self.strategy_parameters.get('TARGET_POINTS', 10)
        self.stoploss_point = self.strategy_parameters.get('STOPLOSS_POINT', 10)

        # Variable that specifies the duration of the scanning candle. For instance, if set to 5, historical data for 5-minute candlesticks will be retrieved. If set to 3, historical data for 3-minute candlesticks will be fetched.
        self.candle_interval_additional_1 = self.strategy_parameters.get('CANDLE_INTERVAL_ADDITIONAL_1', 1440)
        self.candle_interval_additional_2 = self.strategy_parameters.get('CANDLE_INTERVAL_ADDITIONAL_2', 15)

        self.crossover_options_setup_map = {
            1: ('CE', self.strike_direction_ce, self.no_of_strikes_ce),
            -1: ('PE', self.strike_direction_pe, self.no_of_strikes_pe),
            0: None
        }
        self.main_order_map = None

        # Validate expiry flags coming from base class
        # This strategy requires a specific number of allowed expiry dates to function correctly
        number_of_allowed_expiry_dates = 1  # This strategy is designed to work with a single expiry date
        if len(self.get_allowed_expiry_dates()) != number_of_allowed_expiry_dates:
            self.logger.error(f"Invalid number of expiry dates. Expected {number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting.")
            raise ABSystemExit

    def initialize(self):
        super().initialize()
        self.main_order_map = {}

    def _candle_checks(self, instrument, fresh_order_candle):
        hist_data_min = self.get_historical_data(instrument=instrument, candle_size=fresh_order_candle)

        # Check if the latest closing price is higher than the previous high price
        if hist_data_min['close'].iloc[-1] > hist_data_min['high'].iloc[-2]:
            return 1

        # Check if the latest closing price is lower than the previous low price
        if hist_data_min['close'].iloc[-1] < hist_data_min['low'].iloc[-2]:
            return -1

        return 0

    def _check_exit_condition(self, main_order):
        ltp = self.broker.get_ltp(main_order.instrument)
        entry_price = main_order.entry_price
        if ltp > entry_price + self.target_points:
            return_value = True
        elif ltp < entry_price - self.stoploss_point:
            return_value = True
        else:
            return_value = False

        return return_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for base_instrument in instruments_bucket:
            if not self.main_order_map.get(base_instrument):

                # Perform candlestick pattern analysis on the base instrument with the first additional candle interval and second additional candle interval

                candle_check_decision_one = self._candle_checks(base_instrument, self.candle_interval_additional_1)
                candle_check_decision_two = self._candle_checks(base_instrument, self.candle_interval_additional_2)

                if candle_check_decision_one == candle_check_decision_two:

                    if candle_check_decision_one in [1, -1]:
                        tradingsymbol_suffix, strike_direction, number_of_strikes = self.crossover_options_setup_map[candle_check_decision_one]
                        self.options_instruments_set_up_all_expiries(base_instrument, tradingsymbol_suffix, self.broker.get_ltp(base_instrument))
                        child_instrument = self.get_child_instrument_details(base_instrument, tradingsymbol_suffix, strike_direction, number_of_strikes)

                        # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                        self.instruments_mapper.add_mappings(base_instrument, child_instrument)

                        selected_instruments.append(child_instrument)
                        meta.append({'base_instrument': base_instrument, 'action': 'BUY'})  # action is BUY as this is an Options Buying Strategy

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        base_instrument = meta['base_instrument']
        self.main_order_map[base_instrument] = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)

        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for child_instrument in instruments_bucket:

            # Check if the instrument is a child instrument
            if self.instruments_mapper.is_child_instrument(child_instrument):
                base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
                if self.main_order_map.get(base_instrument) is not None:

                    if self._check_exit_condition(self.main_order_map.get(base_instrument)):
                        selected_instruments.append(child_instrument)
                        meta.append({'base_instrument': base_instrument, 'action': 'EXIT'})

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        if meta['action'] == 'EXIT':
            base_instrument = meta['base_instrument']
            self.main_order_map[base_instrument].exit_position()
            self.main_order_map[base_instrument] = None  # clear the map for the base instrument, so it can take new orders for placement in the next cycle depending on signal.
            return True

        return False

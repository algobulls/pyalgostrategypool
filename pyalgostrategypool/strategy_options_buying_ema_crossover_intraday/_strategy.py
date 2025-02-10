"""
    Strategy Description:
        The Strategy Options EMA Crossover trades options based on EMA crossovers, entering a Call Option (CE) on a bullish crossover and a Put Option (PE) on a bearish crossover.
        It dynamically selects ITM, ATM, or OTM strikes and manages entries/exits based on crossover signals.
        This ensures disciplined risk management and optimized trade execution.

    Strategy Resources:
        - strategy specific docs here : https://algobulls.github.io/pyalgotrading/strategies/ema_crossover/
        - generalised docs in detail here : https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
"""

import talib
from pyalgotrading.strategy import StrategyOptionsBase


class StrategyOptionsBuyingEMACrossoverIntraday(StrategyOptionsBase):
    name = 'Strategy Options Buying EMA Crossover Intraday'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.timeperiod1 = self.strategy_parameters['TIMEPERIOD1']
        self.timeperiod2 = self.strategy_parameters['TIMEPERIOD2']
        self.no_of_strikes_ce = self.strategy_parameters['NO_OF_STRIKES_CE']  # The number of strikes away from the current price to select for call options (e.g., 1 strike away, 2 strikes away, etc.).
        self.no_of_strikes_pe = self.strategy_parameters['NO_OF_STRIKES_PE']  # The number of strikes away from the current price to select for put options (e.g., 1 strike away, 2 strikes away, etc.).
        self.strike_direction_ce = StrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_CE']]  # The direction of call option strikes, which determines whether to select
                                                                                                        # In-The-Money (ITM), Out-Of-The-Money (OTM), or At-The-Money (ATM) strikes.
        self.strike_direction_pe = StrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_PE']]  # The direction of put option strikes, which determines whether to select
                                                                                                        # In-The-Money (ITM), Out-Of-The-Money (OTM), or At-The-Money (ATM)  strikes.

        # Variable that specifies the duration of the scanning candle. For instance, if set to 5, historical data for 5-minute candlesticks will be retrieved. If set to 3, historical data for 3-minute candlesticks will be fetched.
        self.candle_interval_additional_1 = self.strategy_parameters['CANDLE_INTERVAL_ADDITIONAL_1']

        self.main_order_map = None

        self.crossover_options_setup_map = {
            1: ('CE', self.strike_direction_ce, self.no_of_strikes_ce),
            -1: ('PE', self.strike_direction_pe, self.no_of_strikes_pe),
            0: None
        }

        # Validate expiry flags coming from base class
        # This strategy requires a specific number of allowed expiry dates to function correctly
        number_of_allowed_expiry_dates = 1  # This strategy is designed to work with a single expiry date
        if len(self.get_allowed_expiry_dates()) != number_of_allowed_expiry_dates:
            self.logger.error(f"Invalid number of expiry dates. Expected {number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting.")
            raise ABSystemExit

    def initialize(self):
        super().initialize()
        self.main_order_map = {}

    def get_crossover_value(self, instrument):
        hist_data = self.get_historical_data(instrument=instrument, candle_size=self.candle_interval_additional_1)

        ema_x = talib.EMA(hist_data['close'], timeperiod=self.timeperiod1)
        ema_y = talib.EMA(hist_data['close'], timeperiod=self.timeperiod2)

        crossover_value = self.utils.crossover(ema_x, ema_y)
        return crossover_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for base_instrument in instruments_bucket:
            if not self.main_order_map.get(base_instrument):  # for every base instrument, we allow order placement only if no previous orders, if any, have an open position ie all previous orders are exited, if any.
                crossover = self.get_crossover_value(base_instrument)

                if crossover in [1, -1]:
                    tradingsymbol_suffix, strike_direction, number_of_strikes = self.crossover_options_setup_map[crossover]
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
                    crossover = self.get_crossover_value(base_instrument)

                    if crossover in [1, -1]:
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

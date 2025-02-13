"""
    Strategy Description:
        The Strategy Options RSI PSAR Crossover Intraday combines RSI and PSAR indicators to identify entry signals: a crossover between two RSI periods and a PSAR alignment with the price trend.
        It selects options instruments based on these signals and executes buy orders.
        Positions are exited when either target points or stoploss conditions are met.

    Strategy Resources:
        - strategy specific docs here : https://algobulls.github.io/pyalgotrading/strategies/rsi_macd_crossover/
        - generalised docs in detail here : https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/

"""

import talib
from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyOptionsBase

from strategy.core.strategy_options_base_v2 import OrderTagManager


class StrategyOptionsBuyingRSIPSARCrossoverIntraday(StrategyOptionsBase):
    name = 'Strategy Options Buying RSI PSAR Crossover Intraday'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Initialization of technical indicator parameters
        self.psar_acceleration = self.strategy_parameters['PSAR_ACCELERATION']  # PSAR acceleration factor
        self.psar_max = self.strategy_parameters['PSAR_MAX']  # PSAR maximum value
        self.rsi_period_one = self.strategy_parameters['RSI_PERIOD_ONE']  # Period for first RSI indicator
        self.rsi_period_two = self.strategy_parameters['RSI_PERIOD_TWO']  # Period for second RSI indicator

        # options set-up parameters
        self.no_of_strikes_ce = self.strategy_parameters['NO_OF_STRIKES_CE']  # The number of strikes away from the current price to select for call options (e.g., 1 strike away, 2 strikes away, etc.).
        self.no_of_strikes_pe = self.strategy_parameters['NO_OF_STRIKES_PE']  # The number of strikes away from the current price to select for put options (e.g., 1 strike away, 2 strikes away, etc.).
        self.strike_direction_ce = StrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_CE']]  # Determines the strike selection for call options based on the configured parameter.
                                                                                                        # It maps 0 to In-The-Money (ITM), 1 to At-The-Money (ATM), and 2 to Out-Of-The-Money (OTM) strikes.

        self.strike_direction_pe = StrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_PE']]  # Determines the strike selection for put options based on the configured parameter.
                                                                                                        # It maps 0 to In-The-Money (ITM), 1 to At-The-Money (ATM), and 2 to Out-Of-The-Money (OTM) strikes.

        self.target_points = self.strategy_parameters.get('TARGET_POINTS', 10)
        self.stoploss_point = self.strategy_parameters.get('STOPLOSS_POINT', 10)

        self.crossover_options_setup_map = {
            1: ('CE', self.strike_direction_ce, self.no_of_strikes_ce),
            -1: ('PE', self.strike_direction_pe, self.no_of_strikes_pe),
            0: None
        }

        self.order_tag_manager = None

        # Validate expiry flags coming from base class
        # This strategy requires a specific number of allowed expiry dates to function correctly
        number_of_allowed_expiry_dates = 1  # This strategy is designed to work with a single expiry date
        if len(self.get_allowed_expiry_dates()) != number_of_allowed_expiry_dates:
            self.logger.error(f"Invalid number of expiry dates. Expected {number_of_allowed_expiry_dates}, got {len(self.get_allowed_expiry_dates())}. Exiting.")
            raise ABSystemExit

    def initialize(self):
        super().initialize()

        # Create an instance of OrderTagManager to manage order tags
        self.order_tag_manager = OrderTagManager()

    def _get_decision_rsi(self, instrument):
        hist_data = self.get_historical_data(instrument)
        rsi_series_one = talib.RSI(hist_data['close'], timeperiod=self.rsi_period_one)
        rsi_series_two = talib.RSI(hist_data['close'], timeperiod=self.rsi_period_two)
        crossover_value = self.utils.crossover(rsi_series_one, rsi_series_two, label_one=f'rsi_series_{self.rsi_period_one}', label_two=f'rsi_series_{self.rsi_period_two}')
        return crossover_value

    def _get_decision_psar(self, instrument):
        hist_data = self.get_historical_data(instrument)
        psar_value = talib.SAR(hist_data['high'], hist_data['low'], acceleration=self.psar_acceleration, maximum=self.psar_max)
        if psar_value.iloc[-1] < hist_data['close'].iloc[-1]:
            return 1
        elif psar_value.iloc[-1] > hist_data['close'].iloc[-1]:
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
            base_inst_str = base_instrument.tradingsymbol

            # Fetch existing main orders associated with the base instrument string
            if self.order_tag_manager.get_orders(tags=[base_inst_str], ignore_errors=True):
                continue  # Skip this instrument if main orders already exist

            rsi_crossover = self._get_decision_rsi(base_instrument)
            psar_decision = self._get_decision_psar(base_instrument)
            if rsi_crossover == psar_decision and rsi_crossover in [1, -1]:
                tradingsymbol_suffix, strike_direction, number_of_strikes = self.crossover_options_setup_map[rsi_crossover]
                self.options_instruments_set_up_all_expiries(base_instrument, tradingsymbol_suffix, self.broker.get_ltp(base_instrument))
                child_instrument = self.get_child_instrument_details(base_instrument, tradingsymbol_suffix, strike_direction, number_of_strikes)

                # Map the base instrument to its corresponding child instrument in the instruments' mapper. This allows tracking of relationships between base and child instruments for further processing.
                self.instruments_mapper.add_mappings(base_instrument, child_instrument)

                selected_instruments.append(child_instrument)
                meta.append({'base_inst_str': base_inst_str, 'action': 'BUY'})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        _order = _ = self.broker.OrderRegular(instrument, meta['action'], quantity=self.number_of_lots * instrument.lot_size)

        # add a new order with the specified tags
        self.order_tag_manager.add_order(_order, tags=[meta['base_inst_str']])

        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for child_instrument in instruments_bucket:
            if self.instruments_mapper.is_child_instrument(child_instrument):
                base_instrument = self.instruments_mapper.get_base_instrument(child_instrument)
                base_inst_str = base_instrument.tradingsymbol

                # Fetch existing main orders associated with the base instrument string
                main_order = self.order_tag_manager.get_orders(tags=[base_inst_str], ignore_errors=True)

                if main_order is not None and main_order.get_order_status() is BrokerOrderStatusConstants.COMPLETE:
                    if self._check_exit_condition(main_order):
                        selected_instruments.append(child_instrument)
                        meta.append({'action': 'EXIT', 'base_inst_str': base_inst_str})

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        if meta['action'] == 'EXIT':
            main_order = self.order_tag_manager.get_orders(tags=[meta['base_inst_str']], ignore_errors=True)
            main_order.exit_position()

            # Remove the main order from the order manager
            self.order_tag_manager.remove_order(main_order)

            return True

        return False

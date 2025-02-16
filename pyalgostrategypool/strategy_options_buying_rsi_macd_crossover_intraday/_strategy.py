"""
    Strategy Description:
        The Strategy Options RSI MACD Crossover Intraday enters positions based on RSI and MACD crossovers, buying when RSI crosses above the oversold level and selling when it crosses below the overbought level.
        It uses strike price adjustments for options instruments based on these signals.
        Positions are exited when the opposite crossover signal occurs, indicating a potential reversal.

    Strategy Resources:
        - strategy specific docs here : https://algobulls.github.io/pyalgotrading/strategies/rsi_macd_crossover/
        - generalised docs in detail here : https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/

"""

import talib
from pyalgotrading.constants import *
from pyalgotrading.strategy import StrategyOptionsBase
from strategy.core.strategy_options_base_v2 import OrderTagManager


class StrategyOptionsBuyingRSIMACDCrossoverIntraday(StrategyOptionsBase):
    name = 'Strategy Options Buying RSI MACD Crossover Intraday'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.timeperiod_fast = self.strategy_parameters['TIMEPERIOD_FAST']  # The short time period used for calculating the fast moving average.
        self.timeperiod_slow = self.strategy_parameters['TIMEPERIOD_SLOW']  # The long time period used for calculating the slow moving average.
        self.timeperiod_signal = self.strategy_parameters['TIMEPERIOD_SIGNAL']  # The time period used for calculating the MACD signal line
        self.timeperiod_rsi = self.strategy_parameters['TIMEPERIOD_RSI']  # The time period used for calculating the RSI value
        self.oversold_value = self.strategy_parameters['OVERSOLD_VALUE']  # The RSI value below which an asset is considered oversold.
        self.overbought_value = self.strategy_parameters['OVERBOUGHT_VALUE']  # The RSI value above which an asset is considered overbought.
        self.no_of_strikes_ce = self.strategy_parameters['NO_OF_STRIKES_CE']  # The number of strikes away from the current price to select for call options (e.g., 1 strike away, 2 strikes away, etc.).
        self.no_of_strikes_pe = self.strategy_parameters['NO_OF_STRIKES_PE']  # The number of strikes away from the current price to select for put options (e.g., 1 strike away, 2 strikes away, etc.).
        self.strike_direction_ce = OptionsStrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_CE']]  # Determines the strike selection for call options based on the configured parameter.
                                                                                                        # It maps 0 to In-The-Money (ITM), 1 to At-The-Money (ATM), and 2 to Out-Of-The-Money (OTM) strikes.

        self.strike_direction_pe = OptionsStrikeDirectionMap[self.strategy_parameters['STRIKE_DIRECTION_PE']]  # Determines the strike selection for put options based on the configured parameter.
                                                                                                        # It maps 0 to In-The-Money (ITM), 1 to At-The-Money (ATM), and 2 to Out-Of-The-Money (OTM) strikes.

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

    @staticmethod
    def _get_tradingsymbol_suffix_for_instrument(instrument_obj):
        instrument = instrument_obj.tradingsymbol
        if instrument.endswith('CE') or instrument.endswith('CE [LOCAL]'):
            return 'CE'
        elif instrument.endswith('PE') or instrument.endswith('PE [LOCAL]'):
            return 'PE'
        else:
            return None

    def get_decision(self, instrument):
        hist_data = self.get_historical_data(instrument)
        macdline, macdsignal, _ = talib.MACD(hist_data['close'], fastperiod=self.timeperiod_fast, slowperiod=self.timeperiod_slow, signalperiod=self.timeperiod_signal)
        rsi_value = talib.RSI(macdsignal, timeperiod=self.timeperiod_rsi)

        oversold_list = [self.oversold_value] * rsi_value.size
        overbought_list = [self.overbought_value] * rsi_value.size

        oversold_crossover_value = self.utils.crossover(rsi_value, oversold_list)
        overbought_crossover_value = self.utils.crossover(rsi_value, overbought_list)

        return oversold_crossover_value, overbought_crossover_value

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for base_instrument in instruments_bucket:
            base_inst_str = base_instrument.tradingsymbol

            # Fetch existing main orders associated with the base instrument string
            if self.order_tag_manager.get_orders(tags=[base_inst_str], ignore_errors=True):
                continue  # Skip this instrument if main orders already exist

            oversold_crossover_value, overbought_crossover_value = self.get_decision(base_instrument)

            if oversold_crossover_value == 1:
                tradingsymbol_suffix, strike_direction, number_of_strikes = self.crossover_options_setup_map[oversold_crossover_value]
            elif overbought_crossover_value == -1:
                tradingsymbol_suffix, strike_direction, number_of_strikes = self.crossover_options_setup_map[overbought_crossover_value]
            else:
                continue  # Skip if neither condition is met

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
                    oversold_crossover_value, overbought_crossover_value = self.get_decision(base_instrument)
                    tradingsymbol_suffix = self._get_tradingsymbol_suffix_for_instrument(main_order.instrument)
                    if ((oversold_crossover_value == -1) and tradingsymbol_suffix == 'PE') or ((overbought_crossover_value == 1) and tradingsymbol_suffix == 'CE'):
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

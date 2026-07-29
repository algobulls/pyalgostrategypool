import talib
from constants import *
from strategy.core.strategy_base import StrategyBase
from strategy.core.strategy_options_base_v2 import OrderTagManager
from utils.candlesticks.heikinashi import HeikinAshi
from utils.func import check_argument_bulk, is_positive_int, is_positive_int_or_float

"""
Strategy Description:
    This strategy combines Heikin Ashi candles, EMA, and ADX to identify strong trend continuation opportunities.

    Heikin Ashi is a modified candlestick technique that smooths price action to reduce market noise,
    making it easier to identify trend direction and potential reversals. The strategy looks for the
    first bullish or bearish Heikin Ashi candle following an opposite-colored candle to signal a
    possible continuation of the prevailing trend.

    Long and short entries are confirmed by EMA trend alignment and ADX trend strength.

    Positions remain open until an EMA crossover signals a trend reversal condition is met.
"""


class StrategyEMAADXHeikinAshiTrendIntraday(StrategyBase):
    name = "Strategy EMA ADX HeikinAshi Trend Intraday"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.adx_period = self.strategy_parameters["ADX_PERIOD"]  # The time period used for calculating the Average Directional Index (ADX).
        self.adx_threshold = self.strategy_parameters["ADX_THRESHOLD"]  # The minimum ADX value required to confirm trend strength.
        self.ema_fast_period = self.strategy_parameters["EMA_FAST_PERIOD"]  # The time period used for calculating the fast Exponential Moving Average (EMA).
        self.ema_slow_period = self.strategy_parameters["EMA_SLOW_PERIOD"]  # The time period used for calculating the slow Exponential Moving Average (EMA).

        check_argument_bulk([self.ema_fast_period, self.ema_slow_period, self.adx_period], "extern_function", is_positive_int, "Value should be integer > 0")
        check_argument_bulk([self.adx_threshold], "extern_function", is_positive_int_or_float, "Value should be > 0")

        self.order_tag_manager = None

    def initialize(self):
        self.order_tag_manager = OrderTagManager()

    def _calculate_ema(self, hist_data):
        ema_fast = talib.EMA(hist_data["close"], timeperiod=self.ema_fast_period)
        ema_slow = talib.EMA(hist_data["close"], timeperiod=self.ema_slow_period)
        return ema_fast, ema_slow

    def _get_signal_state(self, instrument, evaluate_exit=False):
        hist_data = self.get_historical_data(instrument)
        hist_data_heikinashi = HeikinAshi(hist_data)

        # Calculate all indicators required for signal generation.
        ema_fast, ema_slow = self._calculate_ema(hist_data)
        adx = talib.ADX(hist_data["high"], hist_data["low"], hist_data["close"], timeperiod=self.adx_period)

        # Identify the colour of the current and previous Heikin Ashi candles.
        previous_ha_green = hist_data_heikinashi["close"].iloc[-2] > hist_data_heikinashi["open"].iloc[-2]
        current_ha_green = hist_data_heikinashi["close"].iloc[-1] > hist_data_heikinashi["open"].iloc[-1]
        previous_ha_red = hist_data_heikinashi["close"].iloc[-2] < hist_data_heikinashi["open"].iloc[-2]
        current_ha_red = hist_data_heikinashi["close"].iloc[-1] < hist_data_heikinashi["open"].iloc[-1]

        # Long entry
        long_entry = (
                ema_fast.iloc[-1] > ema_slow.iloc[-1]  # Fast EMA is above the slow EMA, confirming an uptrend.
                and hist_data["close"].iloc[-1] > ema_slow.iloc[-1]  # Price is trading above the slow EMA.
                and adx.iloc[-1] >= self.adx_threshold  # ADX confirms sufficient trend strength.
                and previous_ha_red  # Previous Heikin Ashi candle is bearish.
                and current_ha_green  # Current Heikin Ashi candle is the first bullish reversal.
        )

        # Short entry
        short_entry = (
                ema_fast.iloc[-1] < ema_slow.iloc[-1]  # Fast EMA is below the slow EMA, confirming a downtrend.
                and hist_data["close"].iloc[-1] < ema_slow.iloc[-1]  # Price is trading below the slow EMA.
                and adx.iloc[-1] >= self.adx_threshold  # ADX confirms sufficient trend strength.
                and previous_ha_green  # Previous Heikin Ashi candle is bullish.
                and current_ha_red  # Current Heikin Ashi candle is the first bearish reversal.
        )

        signal = {"long_entry": long_entry, "short_entry": short_entry}

        if evaluate_exit:

            # Exit positions when the fast EMA crosses the slow EMA in the opposite direction.
            signal["long_exit"] = ema_fast.iloc[-2] >= ema_slow.iloc[-2] and ema_fast.iloc[-1] < ema_slow.iloc[-1]
            signal["short_exit"] = ema_fast.iloc[-2] <= ema_slow.iloc[-2] and ema_fast.iloc[-1] > ema_slow.iloc[-1]

        return signal

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:
            main_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"], ignore_errors=True)
            if main_order:
                continue

            signal = self._get_signal_state(instrument)
            if signal["long_entry"] or signal["short_entry"]:
                selected_instruments.append(instrument)
                meta.append({"action": "BUY" if signal["long_entry"] else "SELL"})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        action = meta["action"]
        quantity = self.number_of_lots * instrument.lot_size

        entry_order = self.broker.OrderRegular(instrument, action, quantity=quantity, order_code=BrokerOrderCodeConstants.INTRADAY)
        if entry_order.get_order_status() != BrokerOrderStatusConstants.COMPLETE:
            return entry_order

        self.order_tag_manager.add_order(entry_order, tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"])

        return entry_order

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:

            main_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"], ignore_errors=True)
            if not main_order:
                continue

            signal = self._get_signal_state(instrument, evaluate_exit=True)
            exit_signal = signal["long_exit"] if main_order.order_transaction_type == BrokerOrderTransactionTypeConstants.BUY else signal["short_exit"]
            if exit_signal:
                selected_instruments.append(instrument)
                meta.append({"action": "EXIT"})

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        action = meta['action']
        if action == "EXIT":
            main_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"], ignore_errors=True)
            if main_order:
                main_order.exit_position()
                self.order_tag_manager.remove_order(main_order)
            return True

        return False

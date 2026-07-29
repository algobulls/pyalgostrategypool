import pandas as pd
import talib
from constants import *
from strategy.core.strategy_base import StrategyBase
from strategy.core.strategy_options_base_v2 import OrderTagManager

from utils.func import check_argument, check_argument_bulk, is_positive_int

"""
Strategy Description:
    This strategy combines Three Line Break price action with MACD to identify trend continuation opportunities.

    Three Line Break is a price-action charting technique that filters minor price fluctuations and
    forms a new bullish or bearish line only when price breaks beyond the previous reversal levels,
    helping identify significant trend changes.

    Long entries are generated when a new bullish Three Line Break is formed and MACD confirms bullish
    momentum. Short entries are generated when a new bearish Three Line Break is formed and MACD
    confirms bearish momentum. Histogram confirmation can optionally be enabled to further filter entries.

    Positions are exited when MACD generates an opposite crossover or when an opposite Three Line Break
    signal is formed, indicating a potential trend reversal.
"""


class StrategyThreeLineBreakMACDTrendIntraday(StrategyBase):
    name = "Strategy Three Line Break MACD Trend Intraday"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.enable_histogram_confirmation = self.strategy_parameters["ENABLE_HISTOGRAM_CONFIRMATION"]  # Enables histogram confirmation for MACD entry signals.
        self.macd_fast_period = self.strategy_parameters["MACD_FAST_PERIOD"]  # The short time period used for calculating the MACD fast moving average.
        self.macd_signal_period = self.strategy_parameters["MACD_SIGNAL_PERIOD"]  # The time period used for calculating the MACD signal line.
        self.macd_slow_period = self.strategy_parameters["MACD_SLOW_PERIOD"]  # The long time period used for calculating the MACD slow moving average.
        self.three_line_break_lines = self.strategy_parameters["THREE_LINE_BREAK_LINES"]  # The number of previous Three Line Break candles used to identify breakout signals.

        check_argument_bulk([self.three_line_break_lines, self.macd_fast_period, self.macd_slow_period, self.macd_signal_period], "extern_function", is_positive_int, "Value should be integer > 0")
        check_argument(self.enable_histogram_confirmation, "extern_function", lambda x: x in [0, 1] and isinstance(x, int), err_message="ENABLE_HISTOGRAM_CONFIRMATION should be 1 to enable and 0 to disable.")

        self.order_tag_manager = None

    def initialize(self):
        self.order_tag_manager = OrderTagManager()

    def _calculate_three_line_break(self, hist_data):
        lines = []
        for _, candle in hist_data.iterrows():
            close = candle["close"]
            if not lines:

                # Initialize the first Three Line Break bar using the first available close.
                lines.append({"open": close, "high": close, "low": close, "close": close, "direction": 0})
                continue

            previous_line = lines[-1]
            previous_close = previous_line["close"]
            previous_direction = previous_line["direction"]

            # Calculate the highest and lowest close among the recent
            # Three Line Break bars used for continuation/reversal checks.
            highest = max(line["close"] for line in lines[-self.three_line_break_lines:])
            lowest = min(line["close"] for line in lines[-self.three_line_break_lines:])

            # Continue the current bullish trend if price closes above the previous Three Line Break close.
            if previous_direction >= 0 and close > previous_close:
                direction = 1

            # Reverse from bullish to bearish only if price breaks below the lowest close of the previous N Three Line Break bars.
            elif previous_direction >= 0 and close < lowest:
                direction = -1

            # Continue the current bearish trend if price closes below the previous Three Line Break close.
            elif previous_direction < 0 and close < previous_close:
                direction = -1

            # Reverse from bearish to bullish only if price breaks above the highest close of the previous N Three Line Break bars.
            elif previous_direction < 0 and close > highest:
                direction = 1

            # No new Three Line Break bar is formed.
            else:
                continue

            # Create the new Three Line Break bar.
            lines.append({"open": previous_close, "high": max(previous_close, close), "low": min(previous_close, close), "close": close, "direction": direction})

        return pd.DataFrame(lines)

    def _get_signal_state(self, instrument, evaluate_exit=False):
        hist_data = self.get_historical_data(instrument)
        tlb_data = self._calculate_three_line_break(hist_data)

        # Calculate MACD on Three Line Break closes.
        macd, macd_signal, histogram = talib.MACD(tlb_data["close"], fastperiod=self.macd_fast_period, slowperiod=self.macd_slow_period, signalperiod=self.macd_signal_period)

        # Identify newly formed bullish and bearish Three Line Break signals.
        new_bullish_line = tlb_data["direction"].iloc[-2] != 1 and tlb_data["direction"].iloc[-1] == 1
        new_bearish_line = tlb_data["direction"].iloc[-2] != -1 and tlb_data["direction"].iloc[-1] == -1

        # Detect the latest MACD crossover direction.
        crossover_value = self.utils.crossover(macd, macd_signal)

        # Long entry
        long_entry = (
                new_bullish_line  # A new bullish Three Line Break has formed.
                and crossover_value == 1  # MACD confirms bullish momentum.
                and (
                        not self.enable_histogram_confirmation
                        or histogram.iloc[-1] > 0  # Histogram confirms bullish momentum (optional).
                )
        )

        # Short entry
        short_entry = (
                new_bearish_line  # A new bearish Three Line Break has formed.
                and crossover_value == -1  # MACD confirms bearish momentum.
                and (
                        not self.enable_histogram_confirmation
                        or histogram.iloc[-1] < 0  # Histogram confirms bearish momentum (optional).
                )
        )

        signal = {"long_entry": long_entry, "short_entry": short_entry}

        if evaluate_exit:

            # Exit long positions on a bearish MACD crossover or a new bearish Three Line Break.
            signal["long_exit"] = (macd.iloc[-2] >= macd_signal.iloc[-2] and macd.iloc[-1] < macd_signal.iloc[-1]) or new_bearish_line

            # Exit short positions on a bullish MACD crossover or a new bullish Three Line Break.
            signal["short_exit"] = (macd.iloc[-2] <= macd_signal.iloc[-2] and macd.iloc[-1] > macd_signal.iloc[-1]) or new_bullish_line

        return signal

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:
            main_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"], ignore_errors=True)
            if main_order:
                continue

            # Evaluate entry conditions
            signal = self._get_signal_state(instrument)
            if signal["long_entry"] or signal["short_entry"]:
                selected_instruments.append(instrument)
                meta.append({"action": ("BUY" if signal["long_entry"] else "SELL")})

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

            # Evaluate exit conditions
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

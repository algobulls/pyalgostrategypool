import pandas as pd
import talib
from constants import *
from strategy.core.strategy_base import StrategyBase
from strategy.core.strategy_options_base_v2 import OrderTagManager

from utils.func import check_argument_bulk, is_positive_int, is_positive_int_or_float

"""
Strategy Description:
    This strategy combines EMA, Bollinger Bands, and Connors RSI to identify high-probability mean
    reversion opportunities.

    Connors RSI is a short-term momentum indicator that combines RSI, price streaks, and rate of
    change to identify extreme overbought and oversold conditions with greater sensitivity than the
    traditional RSI.

    Long entries are generated when price is in a long-term uptrend, closes at or below the lower
    Bollinger Band, Connors RSI indicates an oversold condition, and the current candle closes
    bullish. Short entries are generated under the opposite conditions in a downtrend.

    Positions are exited when price reverts to the Bollinger middle band or when Connors RSI
    indicates that the mean reversion move has largely completed.
"""


class StrategyBollingerConnorsRSIMeanReversionIntraday(StrategyBase):
    name = "Strategy Bollinger Connors RSI Mean Reversion Intraday"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.bollinger_period = self.strategy_parameters["BOLLINGER_PERIOD"]  # The time period used for calculating the Bollinger Bands.
        self.bollinger_standard_deviation = self.strategy_parameters["BOLLINGER_STANDARD_DEVIATION"]  # The number of standard deviations used to calculate the Bollinger Bands.
        self.connors_rsi_long_entry_threshold = self.strategy_parameters["CONNORS_RSI_LONG_ENTRY_THRESHOLD"]  # The maximum Connors RSI value allowed for long entry.
        self.connors_rsi_long_exit_threshold = self.strategy_parameters["CONNORS_RSI_LONG_EXIT_THRESHOLD"]  # The Connors RSI value above which long positions are exited.
        self.connors_rsi_percent_rank_period = self.strategy_parameters["CONNORS_RSI_PERCENT_RANK_PERIOD"]  # The lookback period used to calculate the Percent Rank component of Connors RSI.
        self.connors_rsi_period = self.strategy_parameters["CONNORS_RSI_PERIOD"]  # The time period used for calculating the RSI component of Connors RSI.
        self.connors_rsi_short_entry_threshold = self.strategy_parameters["CONNORS_RSI_SHORT_ENTRY_THRESHOLD"]  # The minimum Connors RSI value required for short entry.
        self.connors_rsi_short_exit_threshold = self.strategy_parameters["CONNORS_RSI_SHORT_EXIT_THRESHOLD"]  # The Connors RSI value below which short positions are exited.
        self.connors_rsi_streak_period = self.strategy_parameters["CONNORS_RSI_STREAK_PERIOD"]  # The time period used for calculating the Streak RSI component of Connors RSI.
        self.ema_period = self.strategy_parameters["EMA_PERIOD"]  # The time period used for calculating the Exponential Moving Average (EMA).

        check_argument_bulk([self.ema_period, self.bollinger_period, self.connors_rsi_period, self.connors_rsi_streak_period, self.connors_rsi_percent_rank_period], "extern_function", is_positive_int, "Value should be integer > 0")
        check_argument_bulk(
            [self.bollinger_standard_deviation, self.connors_rsi_long_entry_threshold, self.connors_rsi_short_entry_threshold, self.connors_rsi_long_exit_threshold, self.connors_rsi_short_exit_threshold], "extern_function",
            is_positive_int_or_float, "Value should be > 0"
        )

        self.order_tag_manager = None

    def initialize(self):
        self.order_tag_manager = OrderTagManager()

    def _calculate_connors_rsi(self, hist_data):
        close = hist_data["close"]

        # Calculate the standard RSI component.
        rsi = talib.RSI(close, timeperiod=self.connors_rsi_period)

        # Calculate the consecutive up/down price streak.
        streak = [0]
        for i in range(1, len(close)):
            if close.iloc[i] > close.iloc[i - 1]:
                streak.append(max(streak[-1], 0) + 1)
            elif close.iloc[i] < close.iloc[i - 1]:
                streak.append(min(streak[-1], 0) - 1)
            else:
                streak.append(0)

        streak = pd.Series(streak, index=close.index)

        # Calculate the RSI of the price streak.
        streak_rsi = talib.RSI(streak, timeperiod=self.connors_rsi_streak_period)

        # Calculate the 1-period Rate of Change (ROC).
        roc = talib.ROC(close, timeperiod=1)

        # Calculate the Percent Rank of the 1-period ROC over the lookback period.
        percent_rank = roc.rolling(window=self.connors_rsi_percent_rank_period, min_periods=self.connors_rsi_percent_rank_period).apply(lambda x: x.rank(pct=True).iloc[-1] * 100, raw=False)

        # Connors RSI is the average of RSI, Streak RSI, and Percent Rank.
        connors_rsi = (rsi + streak_rsi + percent_rank) / 3

        return connors_rsi

    def _get_signal_state(self, instrument, evaluate_exit=False):
        hist_data = self.get_historical_data(instrument)

        # Calculate all indicators required for signal generation.
        ema = talib.EMA(hist_data["close"], timeperiod=self.ema_period)

        upper_band, middle_band, lower_band = talib.BBANDS(hist_data["close"], timeperiod=self.bollinger_period, nbdevup=self.bollinger_standard_deviation, nbdevdn=self.bollinger_standard_deviation, matype=0)
        connors_rsi = self._calculate_connors_rsi(hist_data)

        current_close = hist_data["close"].iloc[-1]
        current_open = hist_data["open"].iloc[-1]

        # Long entry
        long_entry = (
                ema.iloc[-1] < current_close <= lower_band.iloc[-1]  # Price is in a long-term uptrend and closes at or below the lower Bollinger Band.
                and connors_rsi.iloc[-1] < self.connors_rsi_long_entry_threshold  # Connors RSI confirms an oversold condition.
                and current_close > current_open  # Current candle closes bullish.
        )

        # Short entry
        short_entry = (
                ema.iloc[-1] > current_close >= upper_band.iloc[-1]  # Price is in a long-term downtrend and closes at or above the upper Bollinger Band.
                and connors_rsi.iloc[-1] > self.connors_rsi_short_entry_threshold  # Connors RSI confirms an overbought condition.
                and current_close < current_open  # Current candle closes bearish.
        )

        signal = {"long_entry": long_entry, "short_entry": short_entry}

        if evaluate_exit:

            # Exit positions when price reverts to the Bollinger middle band or Connors RSI indicates the mean reversion move has completed.
            signal["long_exit"] = current_close >= middle_band.iloc[-1] or connors_rsi.iloc[-1] > self.connors_rsi_long_exit_threshold
            signal["short_exit"] = current_close <= middle_band.iloc[-1] or connors_rsi.iloc[-1] < self.connors_rsi_short_exit_threshold

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

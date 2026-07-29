import talib
from constants import *
from strategy.core.strategy_base import StrategyBase
from strategy.core.strategy_options_base_v2 import OrderTagManager
from utils.func import check_argument_bulk, is_positive_int, is_positive_int_or_float

"""
Strategy Description:
    This strategy combines the Chandelier Exit indicator with a long-term EMA trend filter to capture sustained market trends.

    The Chandelier Exit is a volatility-based trailing indicator that uses ATR to determine dynamic
    support and resistance levels, helping identify trend reversals while allowing profitable trends
    to continue.

    Long entries are generated when price crosses above the Chandelier Exit while trading above the EMA.
    Short entries are generated when price crosses below the Chandelier Exit while trading below the EMA.

    Positions are managed dynamically using the Chandelier Exit level and are exited when price closes
    beyond the respective Chandelier Exit level or when a session square-off condition is met.
"""


class StrategyChandelierExitTrendWithEMAIntraday(StrategyBase):
    name = "Strategy Chandelier Exit Trend With EMA Intraday"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.atr_period = self.strategy_parameters["ATR_PERIOD"]  # The time period used for calculating the Average True Range (ATR).
        self.chandelier_multiplier = self.strategy_parameters["CHANDELIER_MULTIPLIER"]  # The ATR multiplier used to calculate the Chandelier Exit levels.
        self.chandelier_period = self.strategy_parameters["CHANDELIER_PERIOD"]  # The lookback period used to determine the highest high and lowest low for the Chandelier Exit.
        self.ema_period = self.strategy_parameters["EMA_PERIOD"]  # The time period used for calculating the Exponential Moving Average (EMA).

        check_argument_bulk([self.chandelier_period, self.ema_period, self.atr_period], "extern_function", is_positive_int, "Value should be integer > 0")
        check_argument_bulk([self.chandelier_multiplier], "extern_function", is_positive_int_or_float, "Value should be > 0")

        self.order_tag_manager = None

    def initialize(self):
        self.order_tag_manager = OrderTagManager()

    def _calculate_chandelier_exit(self, hist_data):

        # Calculate ATR to determine the volatility-adjusted Chandelier Exit levels.
        atr = talib.ATR(hist_data["high"], hist_data["low"], hist_data["close"], timeperiod=self.atr_period)

        # Calculate the highest high and lowest low over the configured lookback period.
        highest_high = hist_data["high"].rolling(window=self.chandelier_period, min_periods=self.chandelier_period).max()
        lowest_low = hist_data["low"].rolling(window=self.chandelier_period, min_periods=self.chandelier_period).min()

        # Compute the Chandelier Exit levels for long and short positions.
        long_exit = highest_high - (atr * self.chandelier_multiplier)
        short_exit = lowest_low + (atr * self.chandelier_multiplier)

        return long_exit, short_exit, atr

    def _get_signal_state(self, instrument, evaluate_exit=False):
        hist_data = self.get_historical_data(instrument)

        # Calculate EMA and Chandelier Exit levels.
        ema = talib.EMA(hist_data["close"], timeperiod=self.ema_period)

        long_chandelier, short_chandelier, atr = self._calculate_chandelier_exit(hist_data)

        # Long entry
        long_chandelier_crossover = self.utils.crossover(hist_data["close"], long_chandelier)
        long_entry = (
                long_chandelier_crossover == 1
                and hist_data["close"].iloc[-1] > ema.iloc[-1]  # Price is above the EMA trend filter.
        )

        # Short entry
        short_chandelier_crossover = self.utils.crossover(hist_data["close"], short_chandelier)
        short_entry = (
                short_chandelier_crossover == -1
                and hist_data["close"].iloc[-1] < ema.iloc[-1]  # Price is below the EMA trend filter.
        )

        signal = {"long_entry": long_entry, "short_entry": short_entry}

        if evaluate_exit:

            # Exit long positions when price closes below the Chandelier Exit.
            signal["long_exit"] = hist_data["close"].iloc[-1] < long_chandelier.iloc[-1]

            # Exit short positions when price closes above the Chandelier Exit.
            signal["short_exit"] = hist_data["close"].iloc[-1] > short_chandelier.iloc[-1]

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

            main_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"], ignore_errors=True, )
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

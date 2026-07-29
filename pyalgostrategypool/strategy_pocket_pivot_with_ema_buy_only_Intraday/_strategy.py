import talib
from constants import *
from strategy.core.strategy_base import StrategyBase
from strategy.core.strategy_options_base_v2 import OrderTagManager
from utils.func import check_argument, check_argument_bulk, is_positive_int

"""
Strategy Description:
    This is a long-only Pocket Pivot strategy that identifies institutional accumulation during an
    established uptrend.

    A Pocket Pivot is a volume-based signal that occurs when the current candle's volume exceeds
    the highest down-volume over the previous N trading sessions, indicating potential institutional
    buying before a conventional breakout.

    Entries are taken when the price is above the 50-period SMA, trading above or bouncing from
    the 10-period EMA, and a Pocket Pivot is confirmed by a bullish candle.

    Positions are exited when the price closes below the 10-period EMA or the previous swing low.
"""


class StrategyPocketPivotWithEMABuyOnlyIntraday(StrategyBase):
    name = "Strategy Pocket Pivot With EMA Buy Only Intraday"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.ema_period = self.strategy_parameters["EMA_PERIOD"]  # The time period used for calculating the Exponential Moving Average (EMA).
        self.enable_ema_bounce = self.strategy_parameters["ENABLE_EMA_BOUNCE"]  # Enables entry when price bounces from the EMA.
        self.enable_swing_exit = self.strategy_parameters["ENABLE_SWING_EXIT"]  # Enables exiting positions when price closes below the previous swing low.
        self.pocket_pivot_lookback = self.strategy_parameters["POCKET_PIVOT_LOOKBACK"]  # The lookback period used to determine the highest down-volume for Pocket Pivot confirmation.
        self.sma_period = self.strategy_parameters["SMA_PERIOD"]  # The time period used for calculating the Simple Moving Average (SMA).
        self.swing_lookback = self.strategy_parameters["SWING_LOOKBACK"]  # The lookback period used to identify the previous swing low.

        check_argument_bulk([self.sma_period, self.ema_period, self.pocket_pivot_lookback, self.swing_lookback], "extern_function", is_positive_int, "Value should be integer > 0")

        check_argument(self.enable_ema_bounce, "extern_function", lambda x: x in [0, 1] and isinstance(x, int), err_message="ENABLE_HISTOGRAM_CONFIRMATION should be 1 to enable and 0 to disable.")
        check_argument(self.enable_swing_exit, "extern_function", lambda x: x in [0, 1] and isinstance(x, int), err_message="ENABLE_SWING_EXIT should be 1 to enable and 0 to disable.")

        self.order_tag_manager = None

    def initialize(self):
        self.order_tag_manager = OrderTagManager()

    def _get_signal_state(self, instrument, evaluate_exit=False):
        hist_data = self.get_historical_data(instrument)

        # Calculate all indicators required for signal generation.
        sma = talib.SMA(hist_data["close"], timeperiod=self.sma_period)
        ema = talib.EMA(hist_data["close"], timeperiod=self.ema_period)

        # Calculate the highest down-volume over the previous lookback period.
        highest_down_volume = (hist_data["volume"].where(hist_data["close"] < hist_data["close"].shift(1)).shift(1).rolling(window=self.pocket_pivot_lookback, min_periods=1).max())

        # Calculate the previous swing low for exit confirmation.
        swing_low = hist_data["low"].rolling(window=self.swing_lookback, min_periods=self.swing_lookback).min()

        close = hist_data["close"].iloc[-1]
        open_price = hist_data["open"].iloc[-1]
        low = hist_data["low"].iloc[-1]
        volume = hist_data["volume"].iloc[-1]

        sma_value = sma.iloc[-1]
        ema_value = ema.iloc[-1]
        highest_down_volume_value = highest_down_volume.iloc[-1]
        swing_low_value = swing_low.iloc[-1]

        # Long entry
        long_entry = (
                close > sma_value  # Price is above the long-term SMA, confirming an uptrend.
                and (close > ema_value or (self.enable_ema_bounce and low <= ema_value < close))  # Price is above or bouncing from the EMA.
                and volume > highest_down_volume_value  # Current volume exceeds the highest previous down-volume (Pocket Pivot).
                and close > open_price  # Current candle closes bullish.
        )

        signal = {"long_entry": long_entry}

        # Exit
        if evaluate_exit:
            signal["long_exit"] = (
                    close < ema_value  # Price closes below the EMA.
                    or (self.enable_swing_exit and close < swing_low_value)  # Price closes below the previous swing low.
            )

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
            if signal["long_entry"]:
                selected_instruments.append(instrument)
                meta.append({"action": "BUY"})

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
            if signal["long_exit"]:
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

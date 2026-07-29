import pandas as pd
import talib
from constants import *

from strategy.core.strategy_base import StrategyBase
from strategy.core.strategy_options_base_v2 import OrderTagManager
from strategy.utils import check_order_complete_status, check_order_placed_successfully
from utils.func import check_argument_bulk, is_positive_int, is_positive_int_or_float

"""
Strategy Description:
    This strategy combines the Vortex Indicator, EMA, and ADX to identify strong trending opportunities.
    
    The Vortex Indicator is a trend-following indicator that measures positive and negative price movement.
    A bullish crossover (VI+ crossing above VI-) indicates increasing buying strength, while a bearish
    crossover (VI- crossing above VI+) indicates increasing selling strength.
    Long and short entries are confirmed by EMA trend alignment and ADX trend strength.
    
    At entry, ATR is used to calculate fixed stop-loss and target levels. Positions are exited on
    stop-loss, target, or strategy-defined reversal conditions.
"""


class StrategyVortexTrendWithEMAADXIntraday(StrategyBase):
    name = "Strategy Vortex Trend With EMA ADX Intraday"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.adx_period = self.strategy_parameters.get("ADX_PERIOD", 14)  # The time period used for calculating the Average Directional Index (ADX).
        self.adx_threshold = self.strategy_parameters.get("ADX_THRESHOLD", 25)  # The minimum ADX value required to confirm trend strength.
        self.atr_period = self.strategy_parameters.get("ATR_PERIOD", 14)  # The time period used for calculating the Average True Range (ATR).
        self.ema_fast_period = self.strategy_parameters.get("EMA_FAST_PERIOD", 21)  # The time period used for calculating the fast Exponential Moving Average (EMA).
        self.ema_slow_period = self.strategy_parameters.get("EMA_SLOW_PERIOD", 55)  # The time period used for calculating the slow Exponential Moving Average (EMA).
        self.stoploss_multiplier = self.strategy_parameters.get("STOPLOSS_MULTIPLIER", 5.0)  # The ATR multiplier used to calculate the initial stop-loss level.
        self.target_multiplier = self.strategy_parameters.get("TARGET_MULTIPLIER", 7.0)  # The ATR multiplier used to calculate the initial target level.
        self.vortex_period = self.strategy_parameters.get("VORTEX_PERIOD", 14)  # The time period used for calculating the Vortex Indicator.

        check_argument_bulk([self.vortex_period, self.ema_fast_period, self.ema_slow_period, self.adx_period, self.atr_period], "extern_function", is_positive_int, "Value should be integer > 0")
        check_argument_bulk([self.adx_threshold, self.stoploss_multiplier, self.target_multiplier], "extern_function", is_positive_int_or_float, "Value should be > 0")

        self.order_tag_manager = None

    def initialize(self):
        self.order_tag_manager = OrderTagManager()

    def calculate_ema(self, hist_data):
        ema_fast = talib.EMA(hist_data["close"], timeperiod=self.ema_fast_period)
        ema_slow = talib.EMA(hist_data["close"], timeperiod=self.ema_slow_period)
        return ema_fast, ema_slow

    def calculate_vortex(self, hist_data):
        previous_high = hist_data["high"].shift(1)
        previous_low = hist_data["low"].shift(1)
        previous_close = hist_data["close"].shift(1)

        # Calculate positive and negative Vortex Movement.
        vm_plus = (hist_data["high"] - previous_low).abs()
        vm_minus = (hist_data["low"] - previous_high).abs()

        # Calculate the True Range for each candle.
        tr1 = hist_data["high"] - hist_data["low"]
        tr2 = (hist_data["high"] - previous_close).abs()
        tr3 = (hist_data["low"] - previous_close).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        tr_sum = tr.rolling(window=self.vortex_period, min_periods=self.vortex_period).sum()

        # Calculate rolling sums of positive and negative Vortex Movement.
        vm_plus_sum = vm_plus.rolling(window=self.vortex_period, min_periods=self.vortex_period).sum()
        vm_minus_sum = vm_minus.rolling(window=self.vortex_period, min_periods=self.vortex_period).sum()

        # Compute the Vortex Indicator values.
        vi_plus = vm_plus_sum / tr_sum
        vi_minus = vm_minus_sum / tr_sum

        return vi_plus, vi_minus

    def _get_signal_state(self, instrument):
        hist_data = self.get_historical_data(instrument)

        # Calculate all indicators required for signal generation.
        ema_fast, ema_slow = self.calculate_ema(hist_data)
        adx = talib.ADX(hist_data["high"], hist_data["low"], hist_data["close"], timeperiod=self.adx_period)
        vi_plus, vi_minus = self.calculate_vortex(hist_data)
        atr = talib.ATR(hist_data["high"], hist_data["low"], hist_data["close"], timeperiod=self.atr_period)

        # Detect the latest Vortex crossover direction.
        crossover_value = self.utils.crossover(vi_plus, vi_minus)

        # Long entry
        long_entry = (
                ema_fast.iloc[-1] > ema_slow.iloc[-1]  # Fast EMA is above the slow EMA, confirming an uptrend.
                and adx.iloc[-1] >= self.adx_threshold  # ADX confirms sufficient trend strength.
                and crossover_value == 1  # Bullish Vortex crossover (VI+ crosses above VI-).
        )

        # Short entry
        short_entry = (
                ema_fast.iloc[-1] < ema_slow.iloc[-1]  # Fast EMA is below the slow EMA, confirming a downtrend.
                and adx.iloc[-1] >= self.adx_threshold  # ADX confirms sufficient trend strength.
                and crossover_value == -1  # Bearish Vortex crossover (VI- crosses above VI+).
        )

        return {"long_entry": long_entry, "short_entry": short_entry, "atr": atr.iloc[-1]}

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
                meta.append({"action": "BUY" if signal["long_entry"] else "SELL", "atr": signal["atr"]})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):
        action = meta["action"]
        atr = meta["atr"]
        quantity = self.number_of_lots * instrument.lot_size

        entry_order = self.broker.OrderRegular(instrument, action, quantity=quantity, order_code=BrokerOrderCodeConstants.INTRADAY)
        if entry_order.get_order_status() != BrokerOrderStatusConstants.COMPLETE:
            return entry_order

        self.order_tag_manager.add_order(entry_order, tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"])
        entry_price = entry_order.entry_price
        exit_action = "SELL" if action == "BUY" else "BUY"

        # Calculate stoploss and target price
        if action == "BUY":
            stoploss_price = entry_price - (atr * self.stoploss_multiplier)
            target_price = entry_price + (atr * self.target_multiplier)
        else:
            stoploss_price = entry_price + (atr * self.stoploss_multiplier)
            target_price = entry_price - (atr * self.target_multiplier)

        # Stoploss Order
        if not self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_STOPLOSS_ORDER"], ignore_errors=True):
            stoploss_order = self.broker.OrderRegular(
                instrument, exit_action, quantity=quantity, price=stoploss_price, trigger_price=stoploss_price, order_code=BrokerOrderCodeConstants.INTRADAY,
                order_variety=BrokerOrderVarietyConstants.STOPLOSS_LIMIT, position=BrokerExistingOrderPositionConstants.EXIT, related_order=entry_order
            )
            self.order_tag_manager.add_order(stoploss_order, tags=[f"{instrument.tradingsymbol}_STOPLOSS_ORDER"])

        # Target Order
        if not self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_TARGET_ORDER"], ignore_errors=True):
            target_order = self.broker.OrderRegular(
                instrument, exit_action, quantity=quantity, price=target_price, trigger_price=target_price, order_code=BrokerOrderCodeConstants.INTRADAY,
                order_variety=BrokerOrderVarietyConstants.LIMIT, position=BrokerExistingOrderPositionConstants.EXIT, related_order=entry_order
            )
            self.order_tag_manager.add_order(target_order, tags=[f"{instrument.tradingsymbol}_TARGET_ORDER"])

        return entry_order

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:

            main_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"], ignore_errors=True)
            if not main_order:
                continue

            stoploss_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_STOPLOSS_ORDER"], ignore_errors=True)
            target_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_TARGET_ORDER"], ignore_errors=True)

            # Stoploss Exit
            if stoploss_order and check_order_placed_successfully(stoploss_order) and check_order_complete_status(stoploss_order):
                selected_instruments.append(instrument)
                meta.append({"action": "EXIT"})
                continue

            # Target Exit
            if target_order and check_order_placed_successfully(target_order) and check_order_complete_status(target_order):
                selected_instruments.append(instrument)
                meta.append({"action": "EXIT"})
                continue

            # Reversal Exit
            signal = self._get_signal_state(instrument)
            exit_signal = signal["short_entry"] if main_order.order_transaction_type == BrokerOrderTransactionTypeConstants.BUY else signal["long_entry"]
            if exit_signal:
                selected_instruments.append(instrument)
                meta.append({"action": "EXIT"})

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        action = meta['action']
        if action == "EXIT":

            # Collecting orders
            stoploss_order = self.order_tag_manager.get_orders(tags=[f'{instrument.tradingsymbol}_STOPLOSS_ORDER'], ignore_errors=True)
            target_order = self.order_tag_manager.get_orders(tags=[f'{instrument.tradingsymbol}_TARGET_ORDER'], ignore_errors=True)
            main_order = self.order_tag_manager.get_orders(tags=[f"{instrument.tradingsymbol}_MAIN_ORDER"], ignore_errors=True)

            # Checking order status for each order
            main_order_position_closed = False

            if stoploss_order:
                if check_order_complete_status(stoploss_order):
                    main_order_position_closed = True
                else:
                    stoploss_order.cancel_order()
                self.order_tag_manager.remove_order(stoploss_order)

            if target_order:
                if check_order_complete_status(target_order):
                    main_order_position_closed = True
                else:
                    target_order.cancel_order()

                self.order_tag_manager.remove_order(target_order)

            # Handle main order exit
            if main_order:
                if check_order_complete_status(main_order) and not main_order_position_closed:
                    main_order.exit_position()

                elif not check_order_complete_status(main_order):
                    main_order.cancel_order()

                self.order_tag_manager.remove_order(main_order)
            return True

        # Return False if the exit conditions are not met
        return False

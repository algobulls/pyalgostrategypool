# OBV Breakout Python Strategy Code

"""
    checkout:
        - Strategy docs: (Placeholder link)
        - Generalized OBV strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
        - OBV Breakout Strategy details: see Section 1: OBV Breakout Strategy (Equity)
"""

from pyalgotrading.strategy import StrategyBase
import talib
import numpy as np
import pandas as pd

class OBVBreakoutStrategy(StrategyBase):
    """
    OBV Breakout Strategy with enhanced exit conditions.
    """
    name = 'OBV Breakout'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.obv_ma_period = self.strategy_parameters.get('OBV_MA_PERIOD', 5)
        self.lookback_period = self.strategy_parameters.get('LOOKBACK_PERIOD', 5)
        self.trailing_stop_period = self.strategy_parameters.get('TRAILING_STOP_PERIOD', 5)
        self.atr_period = self.strategy_parameters.get('ATR_PERIOD', 14)
        self.atr_multiplier = self.strategy_parameters.get('ATR_MULTIPLIER', 1.5)
    
    def initialize(self):
        self.positions_data = {}
    
    def compute_obv(self, instrument):
        """
        Compute raw OBV and smoothed OBV (SMA of OBV).
        """
        hist_data = self.get_historical_data(instrument)
        if hist_data.empty:
            print(f"Warning: No historical data for {instrument}")
            return pd.Series(), pd.Series()
        obv = talib.OBV(hist_data['close'], hist_data['volume'])
        smoothed_obv = talib.SMA(obv, timeperiod=self.obv_ma_period)
        return obv, smoothed_obv
    
    def is_price_breakout(self, hist_data):
        """
        Check if today's closing price >= max of last N highs.
        """
        if len(hist_data) < self.lookback_period:
            return False
        latest_close = hist_data['close'].iloc[-1]
        recent_highs = hist_data['high'].iloc[-self.lookback_period:]
        max_high = recent_highs.max()
        condition = latest_close >= max_high
        return condition
    
    def is_obv_breakout(self, smoothed_obv):
        """
        Check if current smoothed OBV >= max of last N smoothed OBV.
        """
        if len(smoothed_obv) < self.lookback_period:
            return False
        latest_smoothed_obv = smoothed_obv.iloc[-1]
        recent_smoothed = smoothed_obv.iloc[-self.lookback_period:]
        max_obv = recent_smoothed.max()
        condition = latest_smoothed_obv >= max_obv
        return condition
    
    def is_price_breakdown(self, hist_data):
        """
        Check if today's closing price <= min of last N lows.
        """
        if len(hist_data) < self.lookback_period:
            return False
        latest_close = hist_data['close'].iloc[-1]
        recent_lows = hist_data['low'].iloc[-self.lookback_period:]
        min_low = recent_lows.min()
        condition = latest_close <= min_low
        return condition
    
    def is_obv_breakdown(self, smoothed_obv):
        """
        Check if current smoothed OBV <= min of last N smoothed OBV.
        """
        if len(smoothed_obv) < self.lookback_period:
            return False
        latest_smoothed_obv = smoothed_obv.iloc[-1]
        recent_smoothed = smoothed_obv.iloc[-self.lookback_period:]
        min_obv = recent_smoothed.min()
        condition = latest_smoothed_obv <= min_obv
        return condition
    
    def compute_signals(self, instrument):
        """
        Compute and return 'BUY' or 'SELL' entry signal if conditions are met,
        otherwise None.
        """
        obv, smoothed_obv = self.compute_obv(instrument)
        hist_data = self.get_historical_data(instrument)
    
        if hist_data.empty:
            return None
    
        # Long Entry
        if self.is_price_breakout(hist_data) and self.is_obv_breakout(smoothed_obv):
            print(f"[Signal] BUY signal generated for {instrument} at price {hist_data['close'].iloc[-1]}")
            return 'BUY'
        
        # Short Entry
        if self.is_price_breakdown(hist_data) and self.is_obv_breakdown(smoothed_obv):
            print(f"[Signal] SELL signal generated for {instrument} at price {hist_data['close'].iloc[-1]}")
            return 'SELL'
        
        # No new entry signal
        return None
    
    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        """
        Return two lists: (selected_instruments, meta)
         - selected_instruments = [instrument1, instrument2, ...]
         - meta = [ {'action': ...}, {'action': ...}, ... ]
        """
        selected_instruments = []
        meta = []
    
        for instrument in instruments_bucket:
            # Skip if already in a position
            if instrument in self.positions_data:
                continue
    
            signal = self.compute_signals(instrument)
            if signal in ['BUY', 'SELL']:
                selected_instruments.append(instrument)
                meta.append({'action': signal})
    
        return selected_instruments, meta
    
    def strategy_enter_position(self, candle, instrument, meta):
        """
        Place an order and store position data, including entry price.
        """
        action = meta['action']
        if action in ['BUY', 'SELL']:
            # Place the order using the backtest's 'lots' parameter
            self.broker.OrderRegular(
                instrument,
                action,
                quantity=instrument.lot_size  # Assumes 'lot_size' is already configured
            )
            
            # Save position details
            hist_data = self.get_historical_data(instrument)
            if hist_data.empty:
                print(f"Warning: Cannot enter position for {instrument} due to missing data")
                return
            
            entry_price = hist_data['close'].iloc[-1]  # Last close
            self.positions_data[instrument] = {
                'side': 'LONG' if action == 'BUY' else 'SHORT',
                'entry_price': entry_price
            }
            print(f"Entered {action} position for {instrument} at price {entry_price}")
    
    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        """
        Decide if we should exit positions. For both LONG and SHORT positions,
        we check:
            (A) OBV-based exit conditions
            (B) Trailing stop conditions
        """
        selected_instruments = []
        meta = []
    
        for instrument in instruments_bucket:
            if instrument not in self.positions_data:
                continue
            
            # Fetch stored position data
            position_data = self.positions_data[instrument]
            side = position_data['side']
            entry_price = position_data['entry_price']
    
            # Compute OBV & smoothed OBV
            obv, smoothed_obv = self.compute_obv(instrument)
            current_obv = obv.iloc[-1]
            current_smoothed_obv = smoothed_obv.iloc[-1]
    
            # For OBV-based exit
            if len(obv) >= self.lookback_period:
                hist_obv_segment = obv.iloc[-self.lookback_period:]
            else:
                hist_obv_segment = obv
            min_obv = hist_obv_segment.min() if len(hist_obv_segment) > 0 else current_obv
            max_obv = hist_obv_segment.max() if len(hist_obv_segment) > 0 else current_obv
    
            # Compute ATR
            hist_data = self.get_historical_data(instrument)
            atr = talib.ATR(
                hist_data['high'],
                hist_data['low'],
                hist_data['close'],
                timeperiod=self.atr_period
            )
            current_atr = atr.iloc[-1] if len(atr) > 0 else 0.0
    
            close_price = hist_data['close'].iloc[-1]
    
            # Lookback-based trailing stop
            if len(hist_data) >= self.trailing_stop_period:
                recent_lows = hist_data['low'].iloc[-self.trailing_stop_period:]
                recent_highs = hist_data['high'].iloc[-self.trailing_stop_period:]
                trailing_stop_long = recent_lows.min()
                trailing_stop_short = recent_highs.max()
            else:
                trailing_stop_long = hist_data['low'].min()
                trailing_stop_short = hist_data['high'].max()
    
            # Check exit conditions for LONG
            if side == 'LONG':
                obv_exit_condition = (
                    current_obv < current_smoothed_obv or
                    current_obv < min_obv
                )
                stop_exit_condition = (
                    close_price < trailing_stop_long or
                    close_price < (entry_price - self.atr_multiplier * current_atr)
                )
    
                if obv_exit_condition or stop_exit_condition:
                    selected_instruments.append(instrument)
                    meta.append({'action': 'EXIT'})
                    if obv_exit_condition:
                        print(f"EXIT signal generated for {instrument} due to OBV condition.")
                    if stop_exit_condition:
                        print(f"EXIT signal generated for {instrument} due to trailing stop condition.")
    
            # Check exit conditions for SHORT
            elif side == 'SHORT':
                obv_exit_condition = (
                    current_obv > current_smoothed_obv or
                    current_obv > max_obv
                )
                stop_exit_condition = (
                    close_price > trailing_stop_short or
                    close_price > (entry_price + self.atr_multiplier * current_atr)
                )
    
                if obv_exit_condition or stop_exit_condition:
                    selected_instruments.append(instrument)
                    meta.append({'action': 'EXIT'})
                    if obv_exit_condition:
                        print(f"EXIT signal generated for {instrument} due to OBV condition.")
                    if stop_exit_condition:
                        print(f"EXIT signal generated for {instrument} due to trailing stop condition.")
    
        return selected_instruments, meta
    
    def strategy_exit_position(self, candle, instrument, meta):
        """
        Exit the position by closing it, and remove from positions_data.
        """
        if meta['action'] == 'EXIT':
            self.broker.close_position(instrument)
            
            # Clean up stored position info
            if instrument in self.positions_data:
                del self.positions_data[instrument]
                print(f"Exited position for {instrument}")
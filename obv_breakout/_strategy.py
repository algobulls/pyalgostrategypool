#!/usr/bin/env python
# coding: utf-8

# In[ ]:


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

class OBVBreakoutStrategy(StrategyBase):
    name = 'OBV Breakout' 
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.obv_ma_period = self.strategy_parameters.get('OBV_MA_PERIOD', 10)
        self.lookback_period = self.strategy_parameters.get('LOOKBACK_PERIOD', 20)
        self.trailing_stop_period = self.strategy_parameters.get('TRAILING_STOP_PERIOD', 10)
        self.atr_period = self.strategy_parameters.get('ATR_PERIOD', 14)  # e.g., 14
        self.atr_multiplier = self.strategy_parameters.get('ATR_MULTIPLIER', 2)
        
    def initialize(self):
        # A dictionary to track position info per instrument
        # Example structure:
        # self.positions_data[instrument.symbol] = {
        #    'side': 'LONG' or 'SHORT',
        #    'entry_price': float,
        #    'obv': float,
        #    'smoothed_obv': float
        # }
        self.positions_data = {}

    def compute_obv(self, instrument):
        """
        Compute raw OBV and smoothed OBV (SMA of OBV).
        """
        hist_data = self.get_historical_data(instrument)
        obv = talib.OBV(hist_data['close'], hist_data['volume'])
        smoothed_obv = talib.SMA(obv, timeperiod=self.obv_ma_period)
        return obv, smoothed_obv

    def is_price_breakout(self, hist_data):
        """
        Check if today's closing price > max of last N highs.
        """
        return hist_data['close'][-1] > max(hist_data['high'][-self.lookback_period:])

    def is_obv_breakout(self, smoothed_obv):
        """
        Check if current smoothed OBV > max of last N smoothed OBV.
        """
        return smoothed_obv[-1] > max(smoothed_obv[-self.lookback_period:])

    def is_price_breakdown(self, hist_data):
        """
        Check if today's closing price < min of last N lows.
        """
        return hist_data['close'][-1] < min(hist_data['low'][-self.lookback_period:])

    def is_obv_breakdown(self, smoothed_obv):
        """
        Check if current smoothed OBV < min of last N smoothed OBV.
        """
        return smoothed_obv[-1] < min(smoothed_obv[-self.lookback_period:])

    def compute_signals(self, instrument):
        """
        Compute and return 'BUY' or 'SELL' entry signal if conditions are met,
        otherwise None.
        """
        obv, smoothed_obv = self.compute_obv(instrument)
        hist_data = self.get_historical_data(instrument)

        # --- Long Entry check ---
        if self.is_price_breakout(hist_data) and self.is_obv_breakout(smoothed_obv):
            return 'BUY'
        
        # --- Short Entry check ---
        if self.is_price_breakdown(hist_data) and self.is_obv_breakdown(smoothed_obv):
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
            if instrument.symbol in self.positions_data:
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
            # Place the order
            self.broker.OrderRegular(
                instrument,
                action,
                quantity=self.number_of_lots * instrument.lot_size
            )
            
            # Save position details
            hist_data = self.get_historical_data(instrument)
            entry_price = hist_data['close'][-1]
            
            self.positions_data[instrument.symbol] = {
                'side': 'LONG' if action == 'BUY' else 'SHORT',
                'entry_price': entry_price
            }

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
            if instrument.symbol not in self.positions_data:
                continue
            
            # Fetch stored position data
            position_data = self.positions_data[instrument.symbol]
            side = position_data['side']
            entry_price = position_data['entry_price']

            # Compute OBV & smoothed OBV
            obv, smoothed_obv = self.compute_obv(instrument)
            current_obv = obv[-1]
            current_smoothed_obv = smoothed_obv[-1]

            # For OBV-based exit
            hist_obv_segment = obv[-self.lookback_period:] if len(obv) >= self.lookback_period else obv
            min_obv = np.min(hist_obv_segment) if len(hist_obv_segment) > 0 else current_obv
            max_obv = np.max(hist_obv_segment) if len(hist_obv_segment) > 0 else current_obv

            # Compute ATR
            hist_data = self.get_historical_data(instrument)
            atr = talib.ATR(
                hist_data['high'],
                hist_data['low'],
                hist_data['close'],
                timeperiod=self.atr_period
            )
            current_atr = atr[-1] if len(atr) > 0 else 0.0

            close_price = hist_data['close'][-1]

            # Lookback-based trailing stop
            if len(hist_data['low']) >= self.trailing_stop_period:
                recent_lows = hist_data['low'][-self.trailing_stop_period:]
                recent_highs = hist_data['high'][-self.trailing_stop_period:]
                trailing_stop_long = np.min(recent_lows)
                trailing_stop_short = np.max(recent_highs)
            else:
                trailing_stop_long = min(hist_data['low'])
                trailing_stop_short = max(hist_data['high'])

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

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, meta):
        """
        Exit the position by closing it, and remove from positions_data.
        """
        if meta['action'] == 'EXIT':
            self.broker.close_position(instrument)
            
            # Clean up stored position info
            if instrument.symbol in self.positions_data:
                del self.positions_data[instrument.symbol]


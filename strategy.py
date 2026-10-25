import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

class SCIStrategy:
    """
    Implements SCI's 3-Phase Market Structure Strategy:
    1. Macro Direction (4H 50-EMA Trend Lock)
    2. 1H Indication Breakout (Zone-to-Zone Map)
    3. Retracement to form confirmed Lower High (LH) / Higher Low (HL)
    4. Execution on Rollover with SL anchored directly to LH/HL and 1:3 R:R.
    """
    def __init__(self, pivot_lookback: int = 5, ema_period: int = 50, rr_ratio: float = 3.0):
        self.pivot_lookback = pivot_lookback
        self.ema_period = ema_period
        self.rr_ratio = rr_ratio

    def calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df['high']
        low = df['low']
        close = df['close']
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def evaluate_symbol(self, macro_df: pd.DataFrame, detail_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Evaluates a Volatility Index across Macro and Detail timeframes.
        """
        if macro_df.empty or detail_df.empty or len(detail_df) < 30:
            return {
                "signal": None,
                "bias": "NEUTRAL",
                "status": "Insufficient Data",
                "action": "Wait",
                "details": {}
            }

        # 1. Macro Trend Bias (50 EMA on 4H)
        macro_ema = self.calculate_ema(macro_df['close'], self.ema_period)
        macro_close = macro_df['close'].iloc[-1]
        macro_ema_val = macro_ema.iloc[-1]

        is_macro_bull = macro_close > macro_ema_val
        is_macro_bear = macro_close < macro_ema_val
        bias = "BULL 📈" if is_macro_bull else "BEAR 📉" if is_macro_bear else "NEUTRAL ⚖️"

        # 2. Body-Close Pivots on 1H Detail Timeframe
        df = detail_df.copy()
        df['body_high'] = np.maximum(df['open'], df['close'])
        df['body_low'] = np.minimum(df['open'], df['close'])
        df['atr'] = self.calculate_atr(df, 14)

        # Detect swing highs and lows
        k = self.pivot_lookback
        swing_highs = []
        swing_lows = []

        for i in range(k, len(df) - 1):
            window_high = df['body_high'].iloc[i - k : i + k + 1]
            if len(window_high) == 2 * k + 1 and df['body_high'].iloc[i] == window_high.max():
                swing_highs.append((df.index[i], df['body_high'].iloc[i]))

            window_low = df['body_low'].iloc[i - k : i + k + 1]
            if len(window_low) == 2 * k + 1 and df['body_low'].iloc[i] == window_low.min():
                swing_lows.append((df.index[i], df['body_low'].iloc[i]))

        recent_swing_high = swing_highs[-1][1] if swing_highs else df['high'].iloc[-10:].max()
        recent_swing_low = swing_lows[-1][1] if swing_lows else df['low'].iloc[-10:].min()
        protect_high = swing_highs[-2][1] if len(swing_highs) >= 2 else recent_swing_high
        protect_low = swing_lows[-2][1] if len(swing_lows) >= 2 else recent_swing_low

        curr_close = df['close'].iloc[-1]
        curr_open = df['open'].iloc[-1]
        curr_high = df['high'].iloc[-1]
        curr_low = df['low'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        prev_open = df['open'].iloc[-2]
        prev_high = df['high'].iloc[-2]
        prev_low = df['low'].iloc[-2]
        atr_val = df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else (curr_high - curr_low)

        signal = None
        status = "Scanning"
        action = "Wait"
        entry_price = curr_close
        sl_price = 0.0
        tp_price = 0.0

        # --- BEARISH LOGIC (Exclusively when Macro is Bearish) ---
        if is_macro_bear:
            # Did price retrace upwards to form Lower High (LH)?
            # Condition: price is above recent swing low, below macro protection high
            if curr_close > recent_swing_low and curr_high < protect_high:
                # Check for Exhaustion & Rollover:
                # 1. Bearish candle (close < open)
                # 2. Breaks previous candle's low (close < prev_low)
                # 3. Peak of retracement forms candidate Lower High
                candidate_lh = max(curr_high, prev_high)
                is_lower_high = candidate_lh < protect_high
                is_rollover = curr_close < curr_open and curr_close < prev_low

                if is_lower_high and is_rollover:
                    signal = "SELL"
                    status = "LH Confirmed"
                    action = "SELL NOW 🔴"
                    sl_price = candidate_lh + (atr_val * 0.2)
                    risk_dist = sl_price - entry_price
                    tp_price = entry_price - (risk_dist * self.rr_ratio)
                else:
                    status = "Retracing for LH"
                    action = "Watch LH ⏳"
            elif curr_close <= recent_swing_low:
                status = "Indication Break"
                action = "Wait Retest ⚡"

        # --- BULLISH LOGIC (Exclusively when Macro is Bullish) ---
        elif is_macro_bull:
            if curr_close < recent_swing_high and curr_low > protect_low:
                candidate_hl = min(curr_low, prev_low)
                is_higher_low = candidate_hl > protect_low
                is_rollover = curr_close > curr_open and curr_close > prev_high

                if is_higher_low and is_rollover:
                    signal = "BUY"
                    status = "HL Confirmed"
                    action = "BUY NOW 🟢"
                    sl_price = candidate_hl - (atr_val * 0.2)
                    risk_dist = entry_price - sl_price
                    tp_price = entry_price + (risk_dist * self.rr_ratio)
                else:
                    status = "Retracing for HL"
                    action = "Watch HL ⏳"
            elif curr_close >= recent_swing_high:
                status = "Indication Break"
                action = "Wait Retest ⚡"

        risk_amount = abs(entry_price - sl_price) if sl_price > 0 else 0.0

        return {
            "signal": signal,
            "bias": bias,
            "status": status,
            "action": action,
            "entry_price": round(entry_price, 4),
            "sl_price": round(sl_price, 4),
            "tp_price": round(tp_price, 4),
            "risk_dist": round(risk_amount, 4),
            "current_price": round(curr_close, 4)
        }

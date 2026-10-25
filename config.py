import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Deriv API Settings
    # App ID 1089 is Deriv's official generic public App ID for testing/third-party apps
    DERIV_APP_ID = int(os.getenv("DERIV_APP_ID", "1089"))
    DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")
    DERIV_WS_URL = os.getenv("DERIV_WS_URL", f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}")

    # Watchlist of Volatility Indices
    # R_10: Volatility 10 Index, R_25: Vol 25, R_50: Vol 50, R_75: Vol 75, R_100: Vol 100
    # 1HZ100V, 1HZ75V, 1HZ50V, 1HZ25V, 1HZ10V: 1-second Tick Volatility Indices
    WATCHLIST = os.getenv(
        "WATCHLIST", 
        "R_100,R_75,R_50,R_25,R_10,1HZ100V,1HZ75V,1HZ50V"
    ).split(",")

    # Strategy Timeframes (in seconds)
    # Granularity: 300 = 5m, 900 = 15m, 3600 = 1h, 14400 = 4h, 86400 = 1d
    MACRO_TIMEFRAME = int(os.getenv("MACRO_TIMEFRAME", "14400"))  # 4-Hour for Macro Trend
    DETAIL_TIMEFRAME = int(os.getenv("DETAIL_TIMEFRAME", "3600"))  # 1-Hour for Structure / Entry

    # Strategy Parameters
    PIVOT_LOOKBACK = int(os.getenv("PIVOT_LOOKBACK", "5"))
    MACRO_EMA_PERIOD = int(os.getenv("MACRO_EMA_PERIOD", "50"))
    RR_RATIO = float(os.getenv("RR_RATIO", "3.0"))  # 1:3 Take Profit target
    BREAKEVEN_R = float(os.getenv("BREAKEVEN_R", "1.5"))  # Auto-breakeven at 1.5R
    STAKE_AMOUNT = float(os.getenv("STAKE_AMOUNT", "10.0"))  # Stake per trade in USD
    MULTIPLIER = int(os.getenv("MULTIPLIER", "100"))  # Multiplier leverage (e.g. x100, x200)

    # Server Port for Render hosting
    PORT = int(os.getenv("PORT", "10000"))

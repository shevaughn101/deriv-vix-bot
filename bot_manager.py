import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from config import Config
from deriv_client import DerivClient
from strategy import SCIStrategy

logger = logging.getLogger("BotManager")

class BotManager:
    def __init__(self):
        self.client = DerivClient()
        self.strategy = SCIStrategy(
            pivot_lookback=Config.PIVOT_LOOKBACK,
            ema_period=Config.MACRO_EMA_PERIOD,
            rr_ratio=Config.RR_RATIO
        )
        
        # State Tracking
        self.scanner_results: Dict[str, Dict[str, Any]] = {}
        self.active_trade: Optional[Dict[str, Any]] = None
        self.trade_history: List[Dict[str, Any]] = []
        
        # Performance Metrics
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.breakevens = 0
        self.net_pnl_usd = 0.0
        self.net_r = 0.0
        
        self.is_running = True
        self.loop_task: Optional[asyncio.Task] = None

    async def start(self):
        """Starts the bot connection and scanning loop."""
        logger.info("Starting SCI Deriv VIX Automated Trading Engine...")
        connected = await self.client.connect()
        if not connected:
            logger.warning("Could not establish initial authorized connection. Retrying in background...")

        self.loop_task = asyncio.create_task(self._scan_and_trade_loop())

    async def _scan_and_trade_loop(self):
        """Main periodic loop: scans all VIX symbols and manages open trades."""
        while self.is_running:
            try:
                # 1. Manage Active Trade if one exists
                if self.active_trade:
                    await self._manage_active_trade()

                # 2. Scan All Watchlist Symbols
                await self._scan_market()

                # 3. Wait for next scan cycle (e.g., 30-60 seconds)
                await asyncio.sleep(45)

            except Exception as e:
                logger.error(f"Error in scan loop: {e}")
                await asyncio.sleep(10)

    async def _scan_market(self):
        """Fetches candles and evaluates each VIX symbol in the watchlist."""
        # Ensure WebSocket is connected before scanning
        if not self.client.is_connected:
            logger.info("Connecting to Deriv WebSocket before scan...")
            connected = await self.client.connect()
            if not connected:
                logger.warning("Waiting for connection to Deriv...")
                return

        # If connected but not yet authorized, attempt authorization
        if self.client.is_connected and not self.client.is_authorized and self.client.token:
            await self.client.authorize()

        for symbol in Config.WATCHLIST:
            symbol = symbol.strip()
            try:
                macro_df = await self.client.get_candles(symbol, granularity=Config.MACRO_TIMEFRAME, count=80)
                detail_df = await self.client.get_candles(symbol, granularity=Config.DETAIL_TIMEFRAME, count=80)

                eval_result = self.strategy.evaluate_symbol(macro_df, detail_df)
                self.scanner_results[symbol] = eval_result

                # Check for Valid Trade Trigger (Strict 1-Trade Rule)
                if not self.active_trade and eval_result.get("signal"):
                    signal_type = eval_result["signal"]
                    logger.info(f"🚨 TRIGGER FOUND on {symbol}: {signal_type} | Status: {eval_result['status']}")
                    await self._execute_trade(symbol, eval_result)

            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")

    async def _execute_trade(self, symbol: str, setup: Dict[str, Any]):
        """Executes trade on Deriv Demo account."""
        direction = setup["signal"]
        entry_price = setup["entry_price"]
        sl_price = setup["sl_price"]
        tp_price = setup["tp_price"]
        risk_dist = setup["risk_dist"]
        
        # Calculate USD Stop Loss and Take Profit amounts relative to stake
        # For a standard $10 stake, risk 100% of stake or proportional
        sl_usd = Config.STAKE_AMOUNT
        tp_usd = Config.STAKE_AMOUNT * Config.RR_RATIO

        logger.info(f"Sending Order to Deriv: {symbol} {direction} | Entry: {entry_price} | SL: {sl_price} | TP: {tp_price}")

        order_res = await self.client.buy_contract(
            symbol=symbol,
            direction=direction,
            stake=Config.STAKE_AMOUNT,
            multiplier=Config.MULTIPLIER,
            stop_loss_usd=sl_usd,
            take_profit_usd=tp_usd
        )

        if order_res.get("success"):
            contract_id = order_res.get("contract_id")
            self.active_trade = {
                "symbol": symbol,
                "direction": direction,
                "contract_id": contract_id,
                "contract_type": order_res.get("type"),
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "risk_dist": risk_dist,
                "be_price": entry_price + (risk_dist * Config.BREAKEVEN_R) if direction == "BUY" else entry_price - (risk_dist * Config.BREAKEVEN_R),
                "is_breakeven": False,
                "open_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "stake": Config.STAKE_AMOUNT,
                "current_profit": 0.0
            }
            logger.info(f"🎯 Trade Active: {symbol} {direction} | Contract ID: {contract_id}")
        else:
            logger.error(f"Order rejected by Deriv: {order_res.get('error')}")

    async def _manage_active_trade(self):
        """Monitors open contract on Deriv for TP, SL, and Breakeven trigger."""
        if not self.active_trade:
            return

        contract_id = self.active_trade["contract_id"]
        status_info = await self.client.get_contract_status(contract_id)
        
        self.active_trade["current_profit"] = status_info.get("profit", 0.0)
        curr_spot = status_info.get("current_spot")
        
        # 1. Check Breakeven Trigger
        if curr_spot and not self.active_trade["is_breakeven"]:
            direction = self.active_trade["direction"]
            be_target = self.active_trade["be_price"]
            
            if (direction == "BUY" and curr_spot >= be_target) or (direction == "SELL" and curr_spot <= be_target):
                self.active_trade["is_breakeven"] = True
                logger.info(f"🔒 Breakeven Triggered on {self.active_trade['symbol']} at {curr_spot}! Trade is now Risk-Free.")

        # 2. Check if Contract has Closed on Deriv
        if status_info.get("is_sold"):
            final_profit = status_info.get("profit", 0.0)
            is_win = final_profit > 0
            is_be = self.active_trade["is_breakeven"] and abs(final_profit) < 1.0

            self.total_trades += 1
            self.net_pnl_usd += final_profit

            outcome = "WIN 🎯" if is_win else "BREAKEVEN 🛡️" if is_be else "LOSS ❌"
            if is_win:
                self.wins += 1
                self.net_r += Config.RR_RATIO
            elif is_be:
                self.breakevens += 1
            else:
                self.losses += 1
                self.net_r -= 1.0

            closed_record = {
                **self.active_trade,
                "final_profit": round(final_profit, 2),
                "outcome": outcome,
                "close_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.trade_history.insert(0, closed_record)
            logger.info(f"🏁 Contract Closed: {self.active_trade['symbol']} | Result: {outcome} (${final_profit})")
            self.active_trade = None

    async def execute_test_trade(self, symbol: str = "R_100", direction: str = "BUY") -> Dict[str, Any]:
        """Manually fires off a test trade on Demo account to verify order execution."""
        if not self.client.is_connected:
            await self.client.connect()
        if not self.client.is_authorized:
            auth_ok = await self.client.authorize()
            if not auth_ok:
                return {
                    "success": False,
                    "error": "Deriv client is not authorized. Please check your API token."
                }

        # Fetch latest price for SL/TP calculation
        df = await self.client.get_candles(symbol, granularity=Config.DETAIL_TIMEFRAME, count=5)
        latest_price = float(df["close"].iloc[-1]) if not df.empty else 1000.0
        risk_dist = max(latest_price * 0.005, 1.0)
        
        setup = {
            "signal": direction.upper(),
            "entry_price": latest_price,
            "sl_price": round(latest_price - risk_dist if direction.upper() == "BUY" else latest_price + risk_dist, 2),
            "tp_price": round(latest_price + (risk_dist * Config.RR_RATIO) if direction.upper() == "BUY" else latest_price - (risk_dist * Config.RR_RATIO), 2),
            "risk_dist": risk_dist
        }
        
        logger.info(f"Manual test trade requested: {symbol} {direction}")
        await self._execute_trade(symbol, setup)
        
        if self.active_trade:
            return {
                "success": True,
                "message": f"Test Demo trade opened on {symbol} ({direction})",
                "trade": self.active_trade
            }
        else:
            return {
                "success": False,
                "error": "Trade submission rejected by Deriv. Check logs."
            }

    async def stop(self):
        """Shuts down bot manager."""
        self.is_running = False
        if self.loop_task:
            self.loop_task.cancel()
        await self.client.close()

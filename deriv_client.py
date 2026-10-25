import json
import asyncio
import logging
from typing import Dict, Any, Optional, List
import pandas as pd
import websockets
from config import Config

logger = logging.getLogger("DerivClient")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class DerivClient:
    def __init__(self, token: str = Config.DERIV_API_TOKEN, app_id: int = Config.DERIV_APP_ID):
        self.token = token
        self.app_id = app_id
        self.ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.account_info: Dict[str, Any] = {}
        self.is_authorized = False
        self.req_id = 0
        self.pending_responses: Dict[int, asyncio.Future] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self.is_running = True

    async def connect(self) -> bool:
        """Establishes WebSocket connection and starts listener."""
        try:
            logger.info(f"Connecting to Deriv WebSocket: {self.ws_url}")
            self.ws = await websockets.connect(self.ws_url, ping_interval=None)
            self._listen_task = asyncio.create_task(self._listener())
            self._ping_task = asyncio.create_task(self._ping_loop())
            
            if self.token:
                auth_success = await self.authorize()
                return auth_success
            else:
                logger.warning("No DERIV_API_TOKEN provided. Running in view-only / unauthenticated mode.")
                return True
        except Exception as e:
            logger.error(f"Failed to connect to Deriv WebSocket: {e}")
            return False

    async def _listener(self):
        """Continuously reads incoming messages from WebSocket."""
        try:
            async for raw_msg in self.ws:
                data = json.loads(raw_msg)
                req_id = data.get("req_id")
                if req_id in self.pending_responses:
                    future = self.pending_responses.pop(req_id)
                    if not future.done():
                        future.set_result(data)
        except websockets.ConnectionClosed:
            logger.warning("Deriv WebSocket connection closed.")
        except Exception as e:
            logger.error(f"Listener error: {e}")
        finally:
            if self.is_running:
                logger.info("Attempting automatic reconnection in 5 seconds...")
                await asyncio.sleep(5)
                await self.connect()

    async def _ping_loop(self):
        """Keeps connection alive by pinging every 30 seconds."""
        while self.is_running and self.ws and not self.ws.closed:
            try:
                await asyncio.sleep(30)
                await self.send_request({"ping": 1})
            except Exception:
                break

    async def send_request(self, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
        """Sends a JSON request with a unique req_id and awaits the response."""
        if not self.ws or self.ws.closed:
            raise ConnectionError("Deriv WebSocket is not connected.")

        self.req_id += 1
        payload["req_id"] = self.req_id
        future = asyncio.get_event_loop().create_future()
        self.pending_responses[self.req_id] = future

        await self.ws.send(json.dumps(payload))
        return await asyncio.wait_for(future, timeout=timeout)

    async def authorize(self) -> bool:
        """Authorizes user using the Deriv Demo API token."""
        try:
            res = await self.send_request({"authorize": self.token})
            if "error" in res:
                logger.error(f"Deriv Authorization failed: {res['error']['message']}")
                self.is_authorized = False
                return False

            auth_data = res.get("authorize", {})
            account_list = auth_data.get("account_list", [])
            
            # Check if current authorized account is Demo (Virtual)
            is_virtual = bool(auth_data.get("is_virtual", 0))
            
            # If authorized on a Real account, look for the Demo account in account_list
            if not is_virtual and account_list:
                for acc in account_list:
                    if acc.get("is_virtual") == 1:
                        demo_token = acc.get("token")
                        if demo_token and demo_token != self.token:
                            logger.info(f"Switching from Real to Demo account ({acc.get('loginid')})...")
                            switch_res = await self.send_request({"authorize": demo_token})
                            if "authorize" in switch_res:
                                auth_data = switch_res["authorize"]
                                is_virtual = True
                                break

            self.account_info = {
                "loginid": auth_data.get("loginid"),
                "balance": float(auth_data.get("balance", 0.0)),
                "currency": auth_data.get("currency", "USD"),
                "is_virtual": is_virtual,
                "email": auth_data.get("email")
            }
            self.is_authorized = True
            
            if self.account_info["is_virtual"]:
                logger.info(f"🔒 SAFETY ACTIVE: Connected to DEMO (Virtual) Account: {self.account_info['loginid']} | Balance: ${self.account_info['balance']} {self.account_info['currency']}")
            else:
                logger.warning(f"Connected to REAL Account: {self.account_info['loginid']}. If you want Demo, make sure your token is from your virtual account.")
            return True
        except Exception as e:
            logger.error(f"Authorization error: {e}")
            self.is_authorized = False
            return False

    async def get_candles(self, symbol: str, granularity: int = 3600, count: int = 100) -> pd.DataFrame:
        """Fetches historical OHLC candle data for any Deriv Volatility index."""
        try:
            req = {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": count,
                "end": "latest",
                "start": 1,
                "style": "candles",
                "granularity": granularity
            }
            res = await self.send_request(req)
            if "error" in res:
                logger.error(f"Error fetching candles for {symbol}: {res['error']['message']}")
                return pd.DataFrame()

            candles = res.get("candles", [])
            if not candles:
                return pd.DataFrame()

            df = pd.DataFrame(candles)
            df["time"] = pd.to_datetime(df["epoch"], unit="s")
            df = df[["time", "open", "high", "low", "close"]].astype({
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64"
            })
            return df
        except Exception as e:
            logger.error(f"Failed to fetch candles for {symbol}: {e}")
            return pd.DataFrame()

    async def buy_contract(
        self, 
        symbol: str, 
        direction: str, 
        stake: float, 
        multiplier: int = 100,
        stop_loss_usd: Optional[float] = None, 
        take_profit_usd: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Executes a Multiplier trade (CFD-like with SL/TP) on Deriv.
        Falls back to High/Low contract if Multiplier proposal is rejected.
        """
        if not self.is_authorized:
            return {"success": False, "error": "Not authorized with Deriv API"}

        contract_type = "MULTUP" if direction.upper() == "BUY" else "MULTDOWN"
        try:
            # 1. Multiplier Proposal
            proposal_req = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": self.account_info.get("currency", "USD"),
                "symbol": symbol,
                "multiplier": multiplier
            }
            
            # Attach SL and TP if provided
            limit_orders = {}
            if stop_loss_usd and stop_loss_usd > 0:
                limit_orders["stop_loss"] = round(stop_loss_usd, 2)
            if take_profit_usd and take_profit_usd > 0:
                limit_orders["take_profit"] = round(take_profit_usd, 2)
            if limit_orders:
                proposal_req["limit_order"] = limit_orders

            prop_res = await self.send_request(proposal_req)
            
            if "error" not in prop_res and "proposal" in prop_res:
                proposal_id = prop_res["proposal"]["id"]
                buy_res = await self.send_request({"buy": proposal_id, "price": stake})
                if "buy" in buy_res:
                    contract_id = buy_res["buy"]["contract_id"]
                    logger.info(f"✅ Multiplier Trade Executed: {symbol} {direction} | Contract ID: {contract_id} | Stake: ${stake}")
                    return {
                        "success": True,
                        "type": "MULTIPLIER",
                        "contract_id": contract_id,
                        "buy_price": buy_res["buy"]["buy_price"],
                        "details": buy_res["buy"]
                    }

            # 2. Fallback to Rise/Fall contract if Multiplier not accepted
            logger.info(f"Multiplier proposal unaccepted. Executing Rise/Fall Contract for {symbol}...")
            fall_type = "CALL" if direction.upper() == "BUY" else "PUT"
            fallback_req = {
                "buy": 1,
                "price": stake,
                "parameters": {
                    "amount": stake,
                    "basis": "stake",
                    "contract_type": fall_type,
                    "currency": self.account_info.get("currency", "USD"),
                    "duration": 60,
                    "duration_unit": "m",
                    "symbol": symbol
                }
            }
            fall_res = await self.send_request(fallback_req)
            if "buy" in fall_res:
                contract_id = fall_res["buy"]["contract_id"]
                logger.info(f"✅ Rise/Fall Trade Executed: {symbol} {direction} | Contract ID: {contract_id}")
                return {
                    "success": True,
                    "type": "RISE_FALL",
                    "contract_id": contract_id,
                    "buy_price": fall_res["buy"]["buy_price"],
                    "details": fall_res["buy"]
                }
            else:
                return {"success": False, "error": fall_res.get("error", {}).get("message", "Unknown error")}

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_contract_status(self, contract_id: int) -> Dict[str, Any]:
        """Checks the real-time profit and status of an open contract."""
        try:
            res = await self.send_request({"proposal_open_contract": 1, "contract_id": contract_id})
            poc = res.get("proposal_open_contract", {})
            return {
                "is_sold": bool(poc.get("is_sold", 0)),
                "profit": float(poc.get("profit", 0.0)),
                "status": poc.get("status", "open"),
                "current_spot": poc.get("current_spot"),
                "entry_spot": poc.get("entry_spot")
            }
        except Exception as e:
            logger.error(f"Failed to check contract status: {e}")
            return {"is_sold": False, "profit": 0.0, "status": "unknown"}

    async def close(self):
        """Clean shutdown."""
        self.is_running = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._ping_task:
            self._ping_task.cancel()
        if self.ws and not self.ws.closed:
            await self.ws.close()

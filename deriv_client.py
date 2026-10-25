import json
import asyncio
import logging
import ssl
from typing import Dict, Any, Optional
import pandas as pd
import websockets
from config import Config

logger = logging.getLogger("DerivClient")

class DerivClient:
    def __init__(self, token: str = Config.DERIV_API_TOKEN, app_id: str = Config.DERIV_APP_ID):
        self.token = token.strip() if token else ""
        self.app_id = str(app_id).strip()
        self.endpoints = [
            f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
            f"wss://ws.binaryws.com/websockets/v3?app_id={self.app_id}"
        ]
        self.current_endpoint_idx = 0
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.account_info: Dict[str, Any] = {}
        self.is_authorized = False
        self.req_id = 0
        self.pending_responses: Dict[int, asyncio.Future] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self.is_running = True
        self._connecting = False

    @property
    def ws_url(self) -> str:
        return self.endpoints[self.current_endpoint_idx]

    async def connect(self) -> bool:
        """Establishes resilient WebSocket connection and starts background workers."""
        if self._connecting:
            return False
        self._connecting = True
        
        # Clean up any dead connection
        if self.ws and not self.ws.closed:
            try:
                await self.ws.close()
            except Exception:
                pass

        ssl_ctx = ssl.create_default_context()
        
        for attempt in range(len(self.endpoints)):
            endpoint = self.endpoints[self.current_endpoint_idx]
            try:
                logger.info(f"Connecting to Deriv Gateway: {endpoint}")
                self.ws = await websockets.connect(
                    endpoint, 
                    ssl=ssl_ctx,
                    ping_interval=None,
                    open_timeout=15,
                    close_timeout=10
                )
                
                # Start background message listener and ping loop
                if self._listen_task and not self._listen_task.done():
                    self._listen_task.cancel()
                if self._ping_task and not self._ping_task.done():
                    self._ping_task.cancel()

                self._listen_task = asyncio.create_task(self._listener())
                self._ping_task = asyncio.create_task(self._ping_loop())
                
                logger.info("Connected to Deriv WebSocket successfully.")
                self._connecting = False

                if self.token:
                    return await self.authorize()
                else:
                    logger.warning("No DERIV_API_TOKEN set in environment. Running in view-only mode.")
                    return True

            except Exception as e:
                logger.warning(f"Connection attempt to {endpoint} failed: {e}. Trying alternate endpoint...")
                self.current_endpoint_idx = (self.current_endpoint_idx + 1) % len(self.endpoints)
                await asyncio.sleep(2)

        self._connecting = False
        logger.error("All Deriv endpoints failed to connect. Will retry on next cycle.")
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
            self.is_authorized = False

    async def _ping_loop(self):
        """Keeps connection alive by pinging every 25 seconds."""
        while self.is_running and self.ws and not self.ws.closed:
            try:
                await asyncio.sleep(25)
                await self.send_request({"ping": 1}, timeout=5.0)
            except Exception:
                break

    async def send_request(self, payload: Dict[str, Any], timeout: float = 12.0) -> Dict[str, Any]:
        """Sends a JSON request with a unique req_id and awaits the response."""
        if not self.ws or self.ws.closed:
            connected = await self.connect()
            if not connected or not self.ws or self.ws.closed:
                raise ConnectionError("Deriv WebSocket is not connected.")

        self.req_id += 1
        payload["req_id"] = self.req_id
        future = asyncio.get_event_loop().create_future()
        self.pending_responses[self.req_id] = future

        await self.ws.send(json.dumps(payload))
        return await asyncio.wait_for(future, timeout=timeout)

    async def authorize(self) -> bool:
        """Authorizes user using the Deriv API token and forces Demo mode."""
        if not self.token:
            return False

        try:
            res = await self.send_request({"authorize": self.token})
            if "error" in res:
                logger.error(f"Deriv Authorization failed: {res['error']['message']}")
                self.is_authorized = False
                return False

            auth_data = res.get("authorize", {})
            account_list = auth_data.get("account_list", [])
            is_virtual = bool(auth_data.get("is_virtual", 0))

            # Automatically switch to Demo account if authorized on Real
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
                logger.info(f"🔒 SAFETY ACTIVE: Connected to DEMO Account: {self.account_info['loginid']} | Balance: ${self.account_info['balance']} {self.account_info['currency']}")
            else:
                logger.warning(f"Connected to REAL Account: {self.account_info['loginid']}.")
            return True

        except Exception as e:
            logger.error(f"Authorization error: {e}")
            self.is_authorized = False
            return False

    async def get_candles(self, symbol: str, granularity: int = 3600, count: int = 80) -> pd.DataFrame:
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
                logger.warning(f"Could not fetch candles for {symbol}: {res['error']['message']}")
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
            logger.warning(f"Failed to fetch candles for {symbol}: {e}")
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
        """Executes a Multiplier trade (CFD-like with SL/TP) or Fallback High/Low on Deriv."""
        if not self.is_authorized:
            return {"success": False, "error": "Not authorized with Deriv API"}

        contract_type = "MULTUP" if direction.upper() == "BUY" else "MULTDOWN"
        try:
            proposal_req = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": self.account_info.get("currency", "USD"),
                "symbol": symbol,
                "multiplier": multiplier
            }
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

            # Fallback to Rise/Fall
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
        """Checks real-time profit and status of open contract."""
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
            logger.warning(f"Failed to check contract status: {e}")
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

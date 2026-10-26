import json
import asyncio
import logging
import ssl
from typing import Dict, Any, Optional
import pandas as pd
import websockets
from config import Config

logger = logging.getLogger("DerivClient")

def is_ws_closed(ws) -> bool:
    """Robust check for closed state across websockets <14 and >=14."""
    if ws is None:
        return True
    if hasattr(ws, "closed"):
        return bool(ws.closed)
    if hasattr(ws, "state"):
        try:
            from websockets.protocol import State
            return ws.state != State.OPEN
        except Exception:
            return ws.state != 1
    return False

class DerivClient:
    def __init__(self, token: str = Config.DERIV_API_TOKEN, app_id: str = Config.DERIV_APP_ID):
        self.token = str(token).strip() if token else ""
        self.app_id = str(app_id).strip() if app_id else "34MjkAbFq1p73SzDZDHQg"
        if self.token.startswith("pat_") and (not self.app_id or self.app_id == "1089"):
            self.app_id = "34MjkAbFq1p73SzDZDHQg"
        self.endpoints = [
            f"wss://red.derivws.com/websockets/v3?app_id={self.app_id}",
            f"wss://blue.derivws.com/websockets/v3?app_id={self.app_id}",
            f"wss://green.derivws.com/websockets/v3?app_id={self.app_id}",
            f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
            f"wss://ws.binaryws.com/websockets/v3?app_id={self.app_id}"
        ]
        self.current_endpoint_idx = 0
        self.ws: Optional[Any] = None
        self.account_info: Dict[str, Any] = {}
        self.is_authorized = False
        self.req_id = 0
        self.pending_responses: Dict[int, asyncio.Future] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self.is_running = True
        self._connecting = False

    def _fetch_pat_otp_url(self) -> Optional[str]:
        """Fetches Demo account and WebSocket OTP URL using Deriv PAT REST API."""
        try:
            import urllib.request
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Deriv-App-ID": self.app_id,
                "Content-Type": "application/json"
            }
            # 1. Get Accounts
            acc_req = urllib.request.Request("https://api.derivws.com/trading/v1/options/accounts", headers=headers)
            with urllib.request.urlopen(acc_req, timeout=10) as res:
                acc_data = json.loads(res.read().decode()).get("data", [])

            demo_acc = None
            for acc in acc_data:
                if acc.get("account_type") == "demo" or acc.get("group") == "demo":
                    demo_acc = acc
                    break
            if not demo_acc and acc_data:
                demo_acc = acc_data[0]

            if not demo_acc:
                logger.error("No account found for PAT token.")
                return None

            account_id = demo_acc.get("account_id")
            self.account_info = {
                "loginid": account_id,
                "balance": float(demo_acc.get("balance", 0.0)),
                "currency": demo_acc.get("currency", "USD"),
                "is_virtual": True
            }

            # 2. Get OTP
            otp_url = f"https://api.derivws.com/trading/v1/options/accounts/{account_id}/otp"
            otp_req = urllib.request.Request(otp_url, headers=headers, method="POST")
            with urllib.request.urlopen(otp_req, timeout=10) as res:
                otp_data = json.loads(res.read().decode())
                return otp_data.get("data", {}).get("url")
        except Exception as e:
            logger.error(f"PAT OTP error: {e}")
            return None

    @property
    def is_connected(self) -> bool:
        """Returns True if the WebSocket connection is active and open."""
        return not is_ws_closed(self.ws)

    @property
    def ws_url(self) -> str:
        return self.endpoints[self.current_endpoint_idx]

    async def connect(self) -> bool:
        """Establishes resilient WebSocket connection and starts background workers."""
        if self._connecting:
            return False
        self._connecting = True
        
        # Clean up any dead connection
        if not is_ws_closed(self.ws):
            try:
                await self.ws.close()
            except Exception:
                pass

        ssl_ctx = ssl.create_default_context()
        headers = {"Origin": "https://app.deriv.com"}
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        # Special handling for Deriv PAT (Personal Access Token) via Options API
        if self.token and self.token.startswith("pat_"):
            ws_url = self._fetch_pat_otp_url()
            if ws_url:
                try:
                    logger.info("Connecting to Deriv PAT Options Gateway...")
                    self.ws = await websockets.connect(
                        ws_url,
                        ssl=ssl_ctx,
                        ping_interval=None,
                        open_timeout=15,
                        close_timeout=10
                    )
                    self._listen_task = asyncio.create_task(self._listener())
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    self.is_authorized = True
                    logger.info(f"🔒 SAFETY ACTIVE: Connected to DEMO Account: {self.account_info['loginid']} | Balance: ${self.account_info['balance']} {self.account_info['currency']}")
                    self._connecting = False
                    return True
                except Exception as e:
                    logger.warning(f"PAT Gateway connection failed: {e}")
        
        for attempt in range(len(self.endpoints)):
            endpoint = self.endpoints[self.current_endpoint_idx]
            try:
                logger.info(f"Connecting to Deriv Gateway: {endpoint}")
                try:
                    # websockets >= 14
                    self.ws = await websockets.connect(
                        endpoint, 
                        ssl=ssl_ctx,
                        additional_headers=headers,
                        user_agent_header=user_agent,
                        ping_interval=None,
                        open_timeout=15,
                        close_timeout=10
                    )
                except TypeError:
                    # websockets < 14
                    self.ws = await websockets.connect(
                        endpoint, 
                        ssl=ssl_ctx,
                        extra_headers={"User-Agent": user_agent, **headers},
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
        while self.is_running and not is_ws_closed(self.ws):
            try:
                await asyncio.sleep(25)
                await self.send_request({"ping": 1}, timeout=5.0)
            except Exception:
                break

    async def send_request(self, payload: Dict[str, Any], timeout: float = 12.0) -> Dict[str, Any]:
        """Sends a JSON request with a unique req_id and awaits the response."""
        if is_ws_closed(self.ws):
            connected = await self.connect()
            if not connected or is_ws_closed(self.ws):
                raise ConnectionError("Deriv WebSocket is not connected.")

        self.req_id += 1
        payload["req_id"] = self.req_id
        future = asyncio.get_event_loop().create_future()
        self.pending_responses[self.req_id] = future

        await self.ws.send(json.dumps(payload))
        return await asyncio.wait_for(future, timeout=timeout)

    async def authorize(self, new_token: Optional[str] = None) -> bool:
        """Authorizes user using the Deriv API token and forces Demo mode."""
        if new_token:
            self.token = str(new_token).strip()
        if not self.token:
            return False

        if self.token.startswith("pat_"):
            return await self.connect()

        if is_ws_closed(self.ws):
            connected = await self.connect()
            if not connected:
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
                "underlying_symbol": symbol,
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

            # Fallback to Rise/Fall Options
            fall_type = "CALL" if direction.upper() == "BUY" else "PUT"
            fallback_prop = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": fall_type,
                "currency": self.account_info.get("currency", "USD"),
                "underlying_symbol": symbol,
                "symbol": symbol,
                "duration": 15,
                "duration_unit": "m"
            }
            fall_res = await self.send_request(fallback_prop)
            if "proposal" in fall_res:
                p_id = fall_res["proposal"]["id"]
                buy_res = await self.send_request({"buy": p_id, "price": stake})
                if "buy" in buy_res:
                    contract_id = buy_res["buy"]["contract_id"]
                    logger.info(f"✅ Rise/Fall Trade Executed: {symbol} {direction} | Contract ID: {contract_id}")
                    return {
                        "success": True,
                        "type": "RISE_FALL",
                        "contract_id": contract_id,
                        "buy_price": buy_res["buy"]["buy_price"],
                        "details": buy_res["buy"]
                    }
                else:
                    return {"success": False, "error": buy_res.get("error", {}).get("message", "Purchase rejected")}
            else:
                return {"success": False, "error": fall_res.get("error", {}).get("message", "Proposal rejected")}

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
        if not is_ws_closed(self.ws):
            await self.ws.close()

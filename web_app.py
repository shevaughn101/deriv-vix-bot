from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from bot_manager import BotManager
from config import Config

bot = BotManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start Deriv Bot
    await bot.start()
    yield
    # Shutdown: Stop Deriv Bot
    await bot.stop()

app = FastAPI(title="SCI Deriv VIX Automated Trading Engine", lifespan=lifespan)

@app.get("/health")
async def health_check():
    """Health check endpoint for Render 24/7 uptime monitoring."""
    return {
        "status": "healthy",
        "authorized": bot.client.is_authorized,
        "active_trade": bool(bot.active_trade),
        "total_trades": bot.total_trades
    }

@app.get("/api/status")
async def get_status():
    """Returns current live data as JSON."""
    win_rate = (bot.wins / bot.total_trades * 100) if bot.total_trades > 0 else 0.0
    return {
        "account": bot.client.account_info,
        "is_authorized": bot.client.is_authorized,
        "active_trade": bot.active_trade,
        "scanner": bot.scanner_results,
        "last_order_msg": bot.last_order_msg,
        "stats": {
            "total_trades": bot.total_trades,
            "wins": bot.wins,
            "losses": bot.losses,
            "breakevens": bot.breakevens,
            "win_rate": round(win_rate, 1),
            "net_pnl_usd": round(bot.net_pnl_usd, 2),
            "net_r": round(bot.net_r, 1)
        },
        "history": bot.trade_history[:15]
    }

@app.post("/api/test-trade")
async def trigger_test_trade(request: Request):
    """Manually fires off a test demo trade on R_100."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    symbol = data.get("symbol", "R_100")
    direction = data.get("direction", "BUY")
    result = await bot.execute_test_trade(symbol, direction)
    return result

@app.post("/api/set-token")
async def set_token(request: Request):
    """Sets or updates the Deriv API token dynamically."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON"})
    token = data.get("token", "").strip()
    if not token:
        return {"success": False, "error": "Token cannot be empty"}
    bot.client.token = token
    auth_ok = await bot.client.authorize()
    return {
        "success": auth_ok,
        "authorized": bot.client.is_authorized,
        "account": bot.client.account_info,
        "error": "Deriv authorization failed. Please check the token." if not auth_ok else None
    }

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Renders sleek dark-mode trading dashboard matching our Pine Script HUD."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>SCI Deriv VIX Cloud Trading Engine</title>
        <style>
            :root {
                --bg: #0b132b;
                --card-bg: #1c2541;
                --accent: #48cae4;
                --green: #2ec4b6;
                --red: #e71d36;
                --orange: #ff9f1c;
                --text: #f8f9fa;
                --text-muted: #8d99ae;
                --border: #3a506b;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: var(--bg);
                color: var(--text);
                margin: 0;
                padding: 24px;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid var(--border);
                padding-bottom: 16px;
                margin-bottom: 24px;
            }
            .header-title h1 {
                margin: 0;
                font-size: 24px;
                font-weight: 700;
                color: var(--text);
            }
            .header-title p {
                margin: 4px 0 0 0;
                font-size: 14px;
                color: var(--accent);
            }
            .badge {
                padding: 6px 12px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                display: inline-block;
            }
            .badge-demo { background: rgba(72, 202, 228, 0.2); color: var(--accent); border: 1px solid var(--accent); }
            .badge-live { background: rgba(231, 29, 54, 0.2); color: var(--red); border: 1px solid var(--red); }
            .badge-disconnected { background: rgba(141, 153, 174, 0.2); color: var(--text-muted); }
            
            .grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 16px;
                margin-bottom: 24px;
            }
            .card {
                background: var(--card-bg);
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 16px;
            }
            .card-label {
                font-size: 12px;
                color: var(--text-muted);
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 6px;
            }
            .card-value {
                font-size: 24px;
                font-weight: 700;
            }
            .val-green { color: var(--green); }
            .val-red { color: var(--red); }
            .val-accent { color: var(--accent); }

            table {
                width: 100%;
                border-collapse: collapse;
                background: var(--card-bg);
                border-radius: 8px;
                overflow: hidden;
                border: 1px solid var(--border);
                margin-bottom: 24px;
            }
            th, td {
                padding: 12px 16px;
                text-align: left;
                border-bottom: 1px solid var(--border);
            }
            th {
                background: #151d36;
                font-size: 12px;
                color: var(--text-muted);
                text-transform: uppercase;
            }
            tr:hover { background: rgba(255, 255, 255, 0.02); }
            
            .signal-sell { color: var(--red); font-weight: 700; }
            .signal-buy { color: var(--green); font-weight: 700; }
            .signal-wait { color: var(--text-muted); }
            .signal-retrace { color: var(--orange); font-weight: 600; }

            .active-card {
                background: linear-gradient(135deg, #1c2541, #253358);
                border: 2px solid var(--accent);
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 24px;
            }
            .active-card h3 {
                margin: 0 0 12px 0;
                font-size: 16px;
                color: var(--accent);
            }
            .active-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
            }
            .active-item span { display: block; font-size: 12px; color: var(--text-muted); }
            .active-item strong { font-size: 16px; }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="header-title">
                    <h1>SCI Deriv VIX Automated Engine</h1>
                    <p>Lower High / Higher Low Market Structure Bot (24/7 Cloud)</p>
                </div>
                <div id="account-badge">
                    <span class="badge badge-disconnected">Connecting...</span>
                </div>
            </header>
 
            <!-- Action / Test Trade / Token Panel -->
            <div class="card" style="margin-bottom: 20px; background: #162038; border: 1px solid #3a506b;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                    <div>
                        <strong style="font-size: 16px; color: var(--text);">🕹️ Bot Controls</strong>
                        <div id="engine-status" style="font-size: 13px; color: var(--accent); margin-top: 4px; font-weight: 500;">
                            Engine initialized and scanning market...
                        </div>
                    </div>
                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <button id="btn-test-trade" onclick="triggerTestTrade('R_100', 'BUY')" style="background: var(--green); color: #000; border: none; padding: 10px 18px; border-radius: 6px; font-weight: 700; cursor: pointer; font-size: 14px; transition: 0.2s;">
                            ⚡ Fire Test Demo Trade ($10 BUY on R_100)
                        </button>
                    </div>
                </div>

                <!-- Token input box shown if not authorized -->
                <div id="token-box" style="margin-top: 14px; padding-top: 14px; border-top: 1px dashed var(--border); display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                    <span style="font-size: 13px; color: var(--accent);">🔑 Deriv API Token:</span>
                    <input id="token-input" type="password" placeholder="Paste your Deriv API token here..." style="flex: 1; min-width: 260px; padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 14px;" />
                    <button onclick="saveToken()" style="background: var(--accent); color: #000; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px;">
                        Connect Token
                    </button>
                </div>

                <div id="action-msg" style="display: none; margin-top: 12px; padding: 12px; border-radius: 6px; font-size: 13px; font-weight: 600;"></div>
            </div>

            <div class="grid">
                <div class="card">
                    <div class="card-label">Demo Balance</div>
                    <div class="card-value val-accent" id="acc-balance">$0.00</div>
                </div>
                <div class="card">
                    <div class="card-label">Net P&L (USD)</div>
                    <div class="card-value" id="net-pnl">$0.00</div>
                </div>
                <div class="card">
                    <div class="card-label">Net Return (R)</div>
                    <div class="card-value" id="net-r">0.0 R</div>
                </div>
                <div class="card">
                    <div class="card-label">Win Rate</div>
                    <div class="card-value" id="win-rate">0.0%</div>
                </div>
            </div>

            <div id="active-trade-container" style="display: none;">
                <div class="active-card">
                    <h3>🚀 Active Deriv Position</h3>
                    <div class="active-grid">
                        <div class="active-item"><span>Symbol</span><strong id="at-symbol">-</strong></div>
                        <div class="active-item"><span>Direction</span><strong id="at-dir">-</strong></div>
                        <div class="active-item"><span>Floating P&L</span><strong id="at-profit" class="val-green">$0.00</strong></div>
                        <div class="active-item"><span>Entry Price</span><strong id="at-entry">-</strong></div>
                        <div class="active-item"><span>Stop Loss</span><strong id="at-sl" class="val-red">-</strong></div>
                        <div class="active-item"><span>Take Profit (1:3)</span><strong id="at-tp" class="val-green">-</strong></div>
                        <div class="active-item"><span>Status</span><strong id="at-status">-</strong></div>
                    </div>
                </div>
            </div>

            <h2>Live VIX Multi-Asset Scanner</h2>
            <table>
                <thead>
                    <tr>
                        <th>Asset</th>
                        <th>Macro Bias (4H)</th>
                        <th>SCI Phase</th>
                        <th>Action / Signal</th>
                        <th>Current Price</th>
                    </tr>
                </thead>
                <tbody id="scanner-tbody">
                    <tr><td colspan="5" style="text-align: center; color: var(--text-muted);">Scanning Deriv Volatility Indices...</td></tr>
                </tbody>
            </table>

            <h2>Trade History Log</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Direction</th>
                        <th>Entry</th>
                        <th>Result</th>
                        <th>Profit ($)</th>
                    </tr>
                </thead>
                <tbody id="history-tbody">
                    <tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No closed trades yet.</td></tr>
                </tbody>
            </table>
        </div>

        <script>
            async function updateDashboard() {
                try {
                    const res = await fetch('/api/status');
                    const data = await res.json();

                    // 1. Account Badge & Balance & Token Box
                    const badgeContainer = document.getElementById('account-badge');
                    const tokenBox = document.getElementById('token-box');
                    if (data.is_authorized && data.account) {
                        const isVirtual = data.account.is_virtual;
                        badgeContainer.innerHTML = `<span class="badge ${isVirtual ? 'badge-demo' : 'badge-live'}">
                            ${isVirtual ? '🟢 DEMO ACCOUNT' : '🔴 REAL ACCOUNT'}: ${data.account.loginid}
                        </span>`;
                        document.getElementById('acc-balance').innerText = `$${data.account.balance.toFixed(2)}`;
                        if (tokenBox) tokenBox.style.display = 'none';
                    } else {
                        badgeContainer.innerHTML = `<span class="badge badge-disconnected">⚠️ Unauthenticated (Connect Token Below)</span>`;
                        if (tokenBox) tokenBox.style.display = 'flex';
                    }

                    // 2. Performance Stats
                    const stats = data.stats;
                    const pnlElem = document.getElementById('net-pnl');
                    pnlElem.innerText = `${stats.net_pnl_usd >= 0 ? '+$' : '-$'}${Math.abs(stats.net_pnl_usd).toFixed(2)}`;
                    pnlElem.className = `card-value ${stats.net_pnl_usd >= 0 ? 'val-green' : 'val-red'}`;

                    const rElem = document.getElementById('net-r');
                    rElem.innerText = `${stats.net_r >= 0 ? '+' : ''}${stats.net_r.toFixed(1)} R`;
                    rElem.className = `card-value ${stats.net_r >= 0 ? 'val-green' : 'val-red'}`;

                    document.getElementById('win-rate').innerText = `${stats.win_rate}% (${stats.wins}W / ${stats.losses}L / ${stats.breakevens}BE)`;

                    // 2.5 Engine Status Message
                    if (data.last_order_msg) {
                        const esElem = document.getElementById('engine-status');
                        if (esElem) esElem.innerText = data.last_order_msg;
                    }

                    // 3. Active Trade
                    const atContainer = document.getElementById('active-trade-container');
                    if (data.active_trade) {
                        atContainer.style.display = 'block';
                        const at = data.active_trade;
                        document.getElementById('at-symbol').innerText = at.symbol;
                        document.getElementById('at-dir').innerText = at.direction;
                        document.getElementById('at-dir').className = at.direction === 'BUY' ? 'val-green' : 'val-red';
                        document.getElementById('at-entry').innerText = at.entry_price;
                        document.getElementById('at-sl').innerText = at.sl_price;
                        document.getElementById('at-tp').innerText = at.tp_price;
                        document.getElementById('at-status').innerText = at.is_breakeven ? '🔒 Risk-Free (SL to BE)' : '⏳ Active in Market';
                        
                        const profit = at.current_profit || 0.0;
                        const profElem = document.getElementById('at-profit');
                        if (profElem) {
                            profElem.innerText = `${profit >= 0 ? '+$' : '-$'}${Math.abs(profit).toFixed(2)}`;
                            profElem.className = profit >= 0 ? 'val-green' : 'val-red';
                        }
                    } else {
                        atContainer.style.display = 'none';
                    }

                    // 4. Scanner Table
                    const scanBody = document.getElementById('scanner-tbody');
                    if (data.scanner && Object.keys(data.scanner).length > 0) {
                        let html = '';
                        for (const [sym, info] of Object.entries(data.scanner)) {
                            let actClass = 'signal-wait';
                            if (info.action && info.action.includes('SELL')) actClass = 'signal-sell';
                            else if (info.action && info.action.includes('BUY')) actClass = 'signal-buy';
                            else if (info.action && info.action.includes('Watch')) actClass = 'signal-retrace';

                            html += `<tr>
                                <td><strong>${sym}</strong></td>
                                <td>${info.bias || '-'}</td>
                                <td>${info.status || '-'}</td>
                                <td class="${actClass}">${info.action || 'Wait'}</td>
                                <td>${info.current_price || '-'}</td>
                            </tr>`;
                        }
                        scanBody.innerHTML = html;
                    }

                    // 5. History Table
                    const histBody = document.getElementById('history-tbody');
                    if (data.history && data.history.length > 0) {
                        let hHtml = '';
                        for (const h of data.history) {
                            const pClr = h.final_profit >= 0 ? 'val-green' : 'val-red';
                            hHtml += `<tr>
                                <td>${h.close_time}</td>
                                <td><strong>${h.symbol}</strong></td>
                                <td class="${h.direction === 'BUY' ? 'val-green' : 'val-red'}">${h.direction}</td>
                                <td>${h.entry_price}</td>
                                <td>${h.outcome}</td>
                                <td class="${pClr}">${h.final_profit >= 0 ? '+' : ''}$${h.final_profit}</td>
                            </tr>`;
                        }
                        histBody.innerHTML = hHtml;
                    }

                } catch (err) {
                    console.error("Dashboard update error:", err);
                }
            }

            async function triggerTestTrade(symbol, direction) {
                const btn = document.getElementById('btn-test-trade');
                const msgBox = document.getElementById('action-msg');
                btn.disabled = true;
                btn.innerText = "⏳ Submitting Order to Deriv...";
                msgBox.style.display = 'none';

                try {
                    const res = await fetch('/api/test-trade', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ symbol: symbol, direction: direction })
                    });
                    const result = await res.json();
                    msgBox.style.display = 'block';
                    if (result.success) {
                        msgBox.style.background = 'rgba(6, 214, 160, 0.2)';
                        msgBox.style.color = 'var(--green)';
                        msgBox.style.border = '1px solid var(--green)';
                        msgBox.innerHTML = `✅ <strong>Demo Order Placed!</strong> Contract ID: ${result.trade.contract_id} | ${result.trade.symbol} ${result.trade.direction} @ ${result.trade.entry_price}`;
                        updateDashboard();
                    } else {
                        msgBox.style.background = 'rgba(231, 29, 54, 0.2)';
                        msgBox.style.color = 'var(--red)';
                        msgBox.style.border = '1px solid var(--red)';
                        msgBox.innerHTML = `❌ <strong>Order Failed:</strong> ${result.error || 'Check server logs'}`;
                    }
                } catch (e) {
                    msgBox.style.display = 'block';
                    msgBox.style.background = 'rgba(231, 29, 54, 0.2)';
                    msgBox.style.color = 'var(--red)';
                    msgBox.innerHTML = `❌ Error: ${e.message}`;
                } finally {
                    btn.disabled = false;
                    btn.innerText = "⚡ Fire Test Demo Trade ($10 BUY on R_100)";
                }
            }

            async function saveToken() {
                const inp = document.getElementById('token-input');
                const token = inp.value.trim();
                const msgBox = document.getElementById('action-msg');
                if (!token) return;

                msgBox.style.display = 'block';
                msgBox.style.background = 'rgba(72, 202, 228, 0.2)';
                msgBox.style.color = 'var(--accent)';
                msgBox.innerText = "Authorizing with Deriv...";

                try {
                    const res = await fetch('/api/set-token', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ token: token })
                    });
                    const data = await res.json();
                    if (data.success) {
                        msgBox.style.background = 'rgba(6, 214, 160, 0.2)';
                        msgBox.style.color = 'var(--green)';
                        msgBox.style.border = '1px solid var(--green)';
                        msgBox.innerHTML = `✅ Authorized! Connected to DEMO Account: ${data.account.loginid} ($${data.account.balance})`;
                        inp.value = '';
                        updateDashboard();
                    } else {
                        msgBox.style.background = 'rgba(231, 29, 54, 0.2)';
                        msgBox.style.color = 'var(--red)';
                        msgBox.style.border = '1px solid var(--red)';
                        msgBox.innerHTML = `❌ Failed to authorize: ${data.error || 'Invalid token'}`;
                    }
                } catch (e) {
                    msgBox.innerHTML = `❌ Error: ${e.message}`;
                }
            }

            setInterval(updateDashboard, 5000);
            updateDashboard();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    uvicorn.run("web_app:app", host="0.0.0.0", port=Config.PORT, reload=False)

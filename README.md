# 🤖 SCI Deriv VIX Automated Trading Bot

A 24/7 cloud-native Python trading bot that trades **Deriv Volatility Indices** (`R_100`, `R_75`, `R_50`, `R_25`, `R_10`, `1HZ100V`, etc.) using the **SCI Lower High / Higher Low Market Structure Strategy**.

---

## 🌟 Key Features
- **Strict SCI Market Structure Logic**:
  - Macro 4H 50-EMA Trend Lock (Sells only in Bear, Buys only in Bull).
  - 1H Indication Breakout detection.
  - Retracement check (looking for candle exhaustion).
  - Enters on confirmation rollover with Stop Loss anchored directly above the **Lower High (LH)** or below the **Higher Low (HL)**.
  - **1:3 Asymmetric Risk-to-Reward Target**.
  - **Auto-Breakeven at 1.5R** (converts winning trades to risk-free).
- **Multi-Asset VIX Scanner**:
  - Scans Deriv's top Volatility Indices every 45 seconds.
  - Displays live setup phases on an interactive dark-mode web dashboard.
- **Strict 1-Trade Rule**:
  - Never over-trades or flips directions in chop.
  - Only manages one high-conviction trade at a time.
- **24/7 Cloud Ready (Render Free Tier)**:
  - Runs in the cloud on Render without needing your PC turned on.
  - Connects directly to your **Deriv Demo Account** via WebSocket.

---

## 🔑 Step 1: Get Your Free Deriv Demo API Token
1. Log in to your [Deriv Account](https://app.deriv.com) and switch to your **Demo Account** (Virtual balance: $10,000).
2. Go to **Account Settings** $\rightarrow$ **API Token** (or visit: `https://app.deriv.com/account/api-token`).
3. Under **Create New Token**:
   - Give it a name (e.g., `SCI_VIX_BOT`).
   - Check the boxes for:
     - `read`
     - `trade`
   - Click **Create**.
4. Copy the generated token string.

---

## 🚀 Step 2: Deploy to Render for Free (Runs 24/7 with PC Off)

1. Create a free account on [Render.com](https://render.com).
2. Upload this `deriv-vix-bot` folder to your GitHub (either as a public or private repository).
3. On your Render Dashboard:
   - Click **New +** $\rightarrow$ **Web Service**.
   - Connect your GitHub repository.
   - Configure:
     - **Name**: `deriv-vix-bot`
     - **Runtime**: `Python`
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `uvicorn web_app:app --host 0.0.0.0 --port $PORT`
     - **Instance Type**: `Free`
4. Under **Environment Variables**, add:
   - `DERIV_API_TOKEN` = `your_demo_token_here`
   - `DERIV_APP_ID` = `1089`
   - `STAKE_AMOUNT` = `10.0`
5. Click **Deploy Web Service**!

Render will build and start the bot. You will get a free URL (e.g. `https://deriv-vix-bot.onrender.com`).
When you open that URL, you will see your **Live Web Dashboard** showing your Demo Balance, live VIX scanner, active trades, and trade history!

> [!TIP]
> **Keep Render Awake 24/7 for Free:**
> Render's free tier goes to sleep after 15 minutes of no HTTP requests. To keep the bot running 24/7 without interruption:
> 1. Sign up for a free account at [UptimeRobot.com](https://uptimerobot.com).
> 2. Create a **New Monitor** $\rightarrow$ **HTTP(s)**.
> 3. Enter your Render URL: `https://your-bot-name.onrender.com/health`.
> 4. Set interval to **every 5 minutes**.
> This pings your bot every 5 minutes, keeping it alive 24 hours a day, 7 days a week!

---

## 💻 Step 3: (Optional) Run & Test Locally on Your PC

If you want to test it locally before deploying:

```bash
# 1. Open terminal inside the project folder:
cd C:\Users\conta\.gemini\antigravity-ide\scratch\deriv-vix-bot

# 2. Install dependencies:
pip install -r requirements.txt

# 3. Create a .env file from .env.example:
copy .env.example .env
# Edit .env and paste your DERIV_API_TOKEN

# 4. Start the bot:
python web_app.py
```

Open your browser at `http://localhost:10000` to see your live trading dashboard!

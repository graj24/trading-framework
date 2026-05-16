# User Documentation: Autonomous Trading Framework

Welcome to the Autonomous Trading Framework! This tool is designed to act as your personal, automated stock market analyst and trader. It continuously monitors the market, reads the news, analyzes charts, and executes simulated trades based on advanced algorithms and Artificial Intelligence.

## What Does It Do?

1. **Market Research:** Before the market opens, it scans for news, global trends, and corporate earnings to identify the best stocks to watch today.
2. **Technical Analysis:** It calculates complex indicators (like RSI, MACD, and Volume spikes) to understand a stock's momentum.
3. **Smart Decision Making:** Using an advanced AI (LLM) and Machine Learning models, it reviews all the data and decides whether to BUY, SELL, or HOLD a stock.
4. **Automated Paper Trading:** It safely simulates trading ("paper trading") using real-time market data without risking your actual money. It tracks its own performance, wins, and losses.
5. **Intraday Monitoring:** While the market is open, it watches your open positions every 5 minutes. If a stock hits a profit target, hits a stop-loss, or if sudden bad news breaks, it automatically exits the trade.

## Getting Started

### 1. Installation

Ensure you have Python 3.9+ installed on your system.

Navigate to the project directory and install the required dependencies:
```bash
pip install -r requirements.txt
```

### 2. Configuration

The framework uses a `config.yaml` file to control its behavior. Here are the key settings you might want to change:

```yaml
trading:
  mode: paper        # Options: "paper" (simulation) or "live" (real money - EXPERIMENTAL)
  capital: 100000    # Your starting capital in INR for paper trading
  max_positions: 5   # Maximum number of trades to hold at once

watchlist:
  - RELIANCE
  - TCS
  - INFY
  # Add stocks you want the system to monitor here
```

### 3. Environment Variables

Create a `.env` file in the root directory. This is where you store secret keys. If you want Telegram alerts or AI decisions, you'll need API keys:

```ini
# For AI Trading Decisions
GROQ_API_KEY=your_groq_api_key_here

# For Telegram Alerts (Optional)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

## How to Run the Framework

There are two primary ways to run the framework:

### 1. Single Analysis Cycle
To run a one-time analysis of your watchlist, make decisions, and print a P&L summary, simply run:
```bash
python main.py
```
This is great for testing and seeing what the AI thinks about the market *right now*.

### 2. 24/7 Automated Scheduler
To let the framework run completely autonomously, use the scheduler mode. This will run in the background, waking up at specific times (like 9:00 AM for pre-market, 9:15 AM for trading, etc.) to perform tasks.
```bash
python main.py --schedule
```
*Note: Keep this running in a terminal or deploy it to a cloud server to ensure it doesn't miss market events.*

## Understanding the Output

When you run `python main.py`, you will see an output like this:

```
============================================================
Autonomous Trading Framework
Mode: PAPER | Capital: ₹100,000
Watchlist: RELIANCE, TCS, INFY
============================================================
RELIANCE: BUY (conf=85%) — Strong technicals and positive earnings beat.
  → Paper trade opened: a1b2c3d4 | entry ₹2900.0 | SL ₹2871.0 | T ₹2972.5
TCS: HOLD (conf=60%) — Mixed signals — composite 55/100 but filters: trend=sideways
INFY: SKIP (conf=75%) — Weak composite 30/100 or negative sentiment
```

- **BUY:** The system found a good setup and opened a simulated trade. It sets an Entry price, a Stop Loss (SL - to prevent big losses), and a Target (T - to take profits).
- **HOLD:** The system likes the stock but the conditions aren't perfect yet.
- **SKIP:** The system sees bad news or weak trends and is avoiding the stock entirely.

## Tracking Performance

The system saves all simulated trades in a local file called `paper_trades.db`. At the end of every `main.py` run, it will print a "P&L SUMMARY" showing your open positions (unrealized profit/loss) and the win rate of your closed trades.

If you are using the `--schedule` mode, a daily summary report will be generated automatically at 3:30 PM IST.

## Troubleshooting

- **No Trades Opening?** The system is designed to be cautious. If the market is in a "Bear Regime" (downtrend), it will significantly lower its trading activity. Check the logs for "Regime" status.
- **Missing Data Errors?** Sometimes Yahoo Finance (`yfinance`) fails to fetch data. The system will usually retry or skip the stock for that cycle safely.
- **Need help?** Check the `logs/` directory for detailed information on what every agent is doing behind the scenes.

# Stock Sentinel

AI-powered stock monitoring dashboard. Track portfolios, get sentiment analysis from social media, and receive automated alerts with price forecasts.

## What It Does

- **Portfolio tracking** — Create portfolios, add stock holdings, and see live prices from Yahoo Finance
- **OpenRouter AI Intelligence** — Leverages state-of-the-art free-tier LLMs (Meta Llama 3.3 70B, Google Gemini 2.0 Flash Lite) to produce structured financial evaluations (BULLISH, BEARISH, TAKE PROFIT, RISK ALERT)
- **On-demand & Automated Scans** — Run instant diagnostic evaluations directly from portfolios/alerts or let the daily market close cron run automatically
- **Analytics** — Real-time charts showing portfolio distribution, holdings breakdown, and alert trends

## Tech Stack

- **Backend:** Python, FastAPI, Uvicorn, PostgreSQL
- **Data & AI:** yfinance, OpenRouter AI (OpenAI-compatible HTTP client), Pandas, HTTPX
- **Frontend:** HTML/Jinja2, Tailwind CSS, Material Symbols
- **Auth & Ops:** ItsDangerous (Signed Session Cookies), bcrypt, APScheduler

## Quick Start

```bash
# 1. Set up environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Start Postgres (Docker)
docker run --name stocks-postgres \
  -e POSTGRES_USER=aeem \
  -e POSTGRES_PASSWORD=bunymide \
  -e POSTGRES_DB=stocks_db \
  -p 5432:5432 -d postgres

# 3. Configure
cp .env.example .env
# Set NEON_DATABASE_URL, SECRET_KEY, and optionally OPENROUTER_API_KEY

# 4. Run
uvicorn main:app --reload --port 5000
# or python main.py
```

Open `http://localhost:5000/register` to create an account.

## AI Intelligence Pipeline

The automated analysis pipeline evaluates each active holding:

```
Price & Momentum Data → Financial Prompt Construction → OpenRouter Free LLM → Structured Verdict & Actionable Alert
```

If `OPENROUTER_API_KEY` is not configured, the system gracefully uses a built-in quantitative simulation engine without interrupting functionality.

## Project Structure

```
main.py             FastAPI application & ASGI lifespan
app/
  config.py         Pydantic BaseSettings configuration
  dependencies.py   Auth & CSRF dependencies
  services/
    ai.py           OpenRouter AI intelligence engine & prompt constructor
    cache.py        Tier-1 in-memory & Memcached caching
    market.py       Market data & price synchronization
    workflow.py     Automated AI analysis pipeline orchestration
  routers/          Modular route handlers (auth, dashboard, portfolios, holdings, alerts, analytics, api)
db.py               PostgreSQL schema + connection pool + get_db dependency
scheduler.py        APScheduler cron job
templates/          Jinja2 pages (dashboard, portfolio, alerts, analytics, etc.)
static/             Favicon, PWA icons, manifest, and assets
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEON_DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Session cookie secret |
| `OPENROUTER_API_KEY` | No | OpenRouter API key (free tier available at openrouter.ai) |
| `OPENROUTER_MODEL` | No | AI Model (default: `meta-llama/llama-3.3-70b-instruct:free`) |
| `CRON_SECRET` | No | Auth token for `/api/run-workflow` |
| `MARKET_CLOSE_HOUR` | No | Scheduler hour (default: 16) |
| `MARKET_CLOSE_MINUTE` | No | Scheduler minute (default: 30) |

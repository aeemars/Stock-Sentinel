# Stock Sentinel

AI-powered stock monitoring dashboard. Track portfolios, get sentiment analysis from social media, and receive automated alerts with price forecasts.

## What It Does

- **Portfolio tracking** — Create portfolios, add stock holdings, and see live prices from Yahoo Finance
- **AI analysis** — Four-agent pipeline (ROMA) fetches prices, scores social sentiment, forecasts trends, and synthesises alerts
- **Automated alerts** — Runs daily at market close; results land on your Alerts page
- **Analytics** — Real-time charts showing portfolio distribution, holdings breakdown, and alert trends

## Tech Stack

- **Backend:** Python, FastAPI, Uvicorn, PostgreSQL
- **Data & AI:** yfinance, Prophet, VADER Sentiment, Pandas, HTTPX
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
# Set NEON_DATABASE_URL, SECRET_KEY, and optionally NEYNAR_API_KEY

# 4. Run
uvicorn main:app --reload --port 5000
# or python main.py
```

Open `http://localhost:5000/register` to create an account.

## ROMA Pipeline

The automated analysis runs four agents in sequence for each holding:

```
PriceAgent → SentimentAgent → ForecastAgent → SynthesizerAgent → Alert
```

See [roma/ROMA.md](roma/ROMA.md) for details.

## Project Structure

```
main.py             FastAPI application & ASGI lifespan
app/
  config.py         Pydantic BaseSettings configuration
  dependencies.py   Auth & CSRF dependencies
  session.py        Signed session cookie middleware & flash messaging
  templates_config.py Jinja2Templates configuration & Flask-compatible globals
  routers/          Modular route handlers (auth, dashboard, portfolios, holdings, alerts, analytics, api)
db.py               PostgreSQL schema + connection pool + get_db dependency
scheduler.py        APScheduler cron job
roma/
  agents.py         PriceAgent, SentimentAgent (HTTPX), ForecastAgent, SynthesizerAgent
  workflow.py       Orchestrates the agent pipeline
templates/          Jinja2 pages (dashboard, portfolio, alerts, analytics, etc.)
static/             Favicon and assets
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEON_DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Session cookie secret |
| `NEYNAR_API_KEY` | No | Farcaster sentiment (via Neynar) |
| `CRON_SECRET` | No | Auth token for `/api/run-workflow` |
| `MARKET_CLOSE_HOUR` | No | Scheduler hour (default: 16) |
| `MARKET_CLOSE_MINUTE` | No | Scheduler minute (default: 30) |

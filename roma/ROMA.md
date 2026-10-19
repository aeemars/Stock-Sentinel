# Stock Sentinel AI Intelligence Engine

Stock Sentinel features an automated AI analysis pipeline powered by **OpenRouter** free-tier LLMs (such as Meta Llama 3.3 70B Instruct and Google Gemini 2.0 Flash Lite), replacing the legacy 4-agent ROMA pipeline.

## Architecture

```
User Holdings & Buy Prices
         │
         ▼
yfinance Price Engine (Current price, 7d/30d performance, P&L %)
         │
         ▼
Prompt Constructor (`app/services/ai.py`)
         │
         ▼
OpenRouter API (Free LLM)
         │  (or built-in quantitative simulation engine if no API key is provided)
         ▼
Structured JSON Verdict
  • Signal: BULLISH / NEUTRAL / BEARISH / TAKE_PROFIT / ACCUMULATE / RISK_ALERT
  • Risk Level: LOW / MEDIUM / HIGH / VOLATILE
  • Executive Summary & Bulleted Observations
         │
         ▼
Alerts Persistence (`alerts` table) & Real-Time UI Badging
```

## How It Triggers

1. **On-Demand**: Click **"Run AI Analysis"** directly from any portfolio page or **"Run AI Scan Now"** on the Alerts feed.
2. **Automated Cron**: Triggers automatically at market close (`16:30` by default, configured via `MARKET_CLOSE_HOUR` and `MARKET_CLOSE_MINUTE`).
3. **Webhook/API**: Triggerable via `POST /api/run-workflow` with `X-Cron-Secret`.

## Graceful Fallback

When `OPENROUTER_API_KEY` is not configured, the service seamlessly falls back to a deterministic quantitative simulation model based on entry prices, momentum, and position drawdowns, ensuring 100% offline availability and instant local testing.

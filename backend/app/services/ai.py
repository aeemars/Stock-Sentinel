"""OpenRouter AI Intelligence Service for Stock Sentinel.

Provides structured financial analysis for portfolio holdings using
free-tier LLMs via the OpenRouter API (OpenAI-compatible).
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


import re

def _build_analysis_prompt(holdings_data: List[Dict[str, Any]]) -> str:
    """Build a structured financial analysis prompt from holdings data."""
    holdings_block = ""
    for h in holdings_data:
        clean_ticker = re.sub(r"[^A-Z0-9.\-]", "", str(h.get('ticker', '')).upper())[:10]
        pnl_str = f"{h['pnl_pct']:+.1f}%" if h.get('pnl_pct') is not None else "0.0% (at entry)"
        currency = h.get('currency', 'USD')
        curr_sym = "₦" if currency == "NGN" else "$"
        buy_val = f"{curr_sym}{h['buy_price']:,.2f}" if h.get('buy_price') is not None else "N/A"
        curr_val = f"{curr_sym}{h['last_price']:,.2f}" if h.get('last_price') is not None else f"Tracking near entry ({buy_val})"
        holdings_block += (
            f"- {clean_ticker} ({currency}): {h['shares']} shares, "
            f"Buy {buy_val}, "
            f"Current {curr_val}, "
            f"P&L {pnl_str}\n"
        )

    return f"""You are a concise financial analyst AI for the Stock Sentinel portfolio tracker.

Analyze the following portfolio holdings and produce a JSON array with one object per holding.

Holdings:
{holdings_block}

For EACH holding, return a JSON object with these exact keys:
- "ticker": string (the ticker symbol)
- "signal": one of "BULLISH", "NEUTRAL", "BEARISH", "TAKE_PROFIT", "ACCUMULATE", "RISK_ALERT"
- "risk_level": one of "LOW", "MEDIUM", "HIGH", "VOLATILE"
- "summary": string (1-2 sentence executive assessment based on the price action relative to the buy price. Always provide a constructive assessment based on entry valuation and sector fundamentals)
- "reasoning": string (2-3 bullet points as a single string, each starting with •)

Guidelines:
- All holdings are denominated in USD ($).
- For cryptocurrency assets (e.g. BTC-USD, ETH-USD, SOL-USD), account for 24/7 trading cycles, higher historical volatility, and standard crypto market risk dynamics.
- If current quote is syncing or tracking near entry, evaluate constructively based on the recorded entry price and business/protocol fundamentals rather than declining to assess.

Respond ONLY with a valid JSON array. No markdown, no explanation outside the JSON."""


def _simulate_analysis(holdings_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rule-based fallback when OpenRouter API key is not configured."""
    results = []
    for h in holdings_data:
        ticker = h.get("ticker", "???")
        buy = h.get("buy_price")
        current = h.get("last_price")
        pnl_pct = h.get("pnl_pct")
        curr_sym = "$"

        # Fallback current to buy if current quote is temporarily syncing
        effective_current = current if current is not None else buy
        effective_pnl_pct = pnl_pct if pnl_pct is not None else (0.0 if buy is not None else None)

        # Determine signal from P&L
        if buy is None and current is None:
            signal = "NEUTRAL"
            risk = "MEDIUM"
            summary = f"{ticker}: Insufficient data to evaluate performance. Set a buy price to enable P&L tracking."
            reasoning = "• No buy price or market price available\n• Unable to compute profit/loss metrics\n• Consider updating holding details"
        elif effective_pnl_pct is None or buy is None:
            signal = "NEUTRAL"
            risk = "LOW"
            summary = f"{ticker} is tracked at entry price of {curr_sym}{buy:,.2f}. Position monitored for directional trends."
            reasoning = f"• Position recorded at entry {curr_sym}{buy:,.2f}\n• Exchange feed syncing; holding is active\n• Reassess on next market update"
        elif effective_pnl_pct > 20:
            signal = "TAKE_PROFIT"
            risk = "MEDIUM"
            summary = f"{ticker} is up {effective_pnl_pct:+.1f}% from your entry at {curr_sym}{buy:,.2f}. Consider securing partial gains."
            reasoning = f"• Strong gain of {effective_pnl_pct:+.1f}% above entry price\n• Current price {curr_sym}{effective_current:,.2f} vs buy {curr_sym}{buy:,.2f}\n• Locking in profits reduces downside exposure"
        elif effective_pnl_pct > 5:
            signal = "BULLISH"
            risk = "LOW"
            summary = f"{ticker} is performing well at {effective_pnl_pct:+.1f}% above your entry. Trend remains constructive."
            reasoning = f"• Positive return of {effective_pnl_pct:+.1f}% from entry\n• Current price {curr_sym}{effective_current:,.2f} holding above buy {curr_sym}{buy:,.2f}\n• Momentum supports continued holding"
        elif effective_pnl_pct > -5:
            signal = "NEUTRAL"
            risk = "LOW"
            summary = f"{ticker} is trading near your entry at {effective_pnl_pct:+.1f}%. Position remains stable."
            reasoning = f"• Trading close to entry price ({effective_pnl_pct:+.1f}%)\n• Current {curr_sym}{effective_current:,.2f} vs buy {curr_sym}{buy:,.2f}\n• Hold and monitor for directional break"
        elif effective_pnl_pct > -15:
            signal = "BEARISH"
            risk = "MEDIUM"
            summary = f"{ticker} is down {effective_pnl_pct:+.1f}% from entry. Monitor closely for further deterioration."
            reasoning = f"• Moderate loss of {effective_pnl_pct:+.1f}% from entry\n• Current {curr_sym}{effective_current:,.2f} below buy {curr_sym}{buy:,.2f}\n• Watch for support levels or catalysts"
        else:
            signal = "RISK_ALERT"
            risk = "HIGH"
            summary = f"{ticker} has declined {effective_pnl_pct:+.1f}% from entry. Review thesis and consider risk management."
            reasoning = f"• Significant drawdown of {effective_pnl_pct:+.1f}%\n• Current {curr_sym}{effective_current:,.2f} well below entry {curr_sym}{buy:,.2f}\n• Reassess position size and stop-loss levels"

        results.append({
            "ticker": ticker,
            "signal": signal,
            "risk_level": risk,
            "summary": summary,
            "reasoning": reasoning,
        })
    return results


def analyze_holdings(holdings_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Analyze holdings using OpenRouter LLM or fall back to rule-based simulation.

    Args:
        holdings_data: List of dicts with keys: ticker, shares, buy_price,
                       last_price, pnl_pct.

    Returns:
        List of analysis dicts with keys: ticker, signal, risk_level, summary,
        reasoning.
    """
    if not holdings_data:
        return []

    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        logger.info("[AI] No OPENROUTER_API_KEY configured; using rule-based simulation")
        return _simulate_analysis(holdings_data)

    prompt = _build_analysis_prompt(holdings_data)

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/aeemars/Stock-Sentinel",
                    "X-Title": "Stock Sentinel",
                },
                json={
                    "model": settings.OPENROUTER_MODEL,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2048,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            content = "\n".join(lines)

        results = json.loads(content)
        if isinstance(results, list):
            logger.info(f"[AI] OpenRouter returned analysis for {len(results)} holdings")
            return results

        logger.warning("[AI] OpenRouter response was not a JSON array; falling back")
        return _simulate_analysis(holdings_data)

    except Exception as e:
        logger.error(f"[AI] OpenRouter API error: {e}; falling back to simulation")
        return _simulate_analysis(holdings_data)


def format_analysis_as_alert(ticker: str, analysis: Dict[str, Any]) -> str:
    """Format a single holding analysis dict into a human-readable alert message."""
    signal = analysis.get("signal", "NEUTRAL")
    risk = analysis.get("risk_level", "MEDIUM")
    summary = analysis.get("summary", "No analysis available.")
    reasoning = analysis.get("reasoning", "")

    lines = [
        f"📊 {ticker} — {signal} (Risk: {risk})",
        "",
        summary,
        "",
        reasoning,
    ]
    return "\n".join(lines)

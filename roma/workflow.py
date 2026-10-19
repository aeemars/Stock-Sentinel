"""Stock Sentinel AI analysis workflow.

Orchestrates the analysis pipeline: fetches price data for each holding,
computes metrics, and calls the OpenRouter AI service (or rule-based
simulation) to produce structured alerts stored in the database.
"""

from .agents import PriceAgent
from db import get_db_connection, put_db_connection
from psycopg2.extras import RealDictCursor
from app.services.ai import analyze_holdings, format_analysis_as_alert
import logging

logger = logging.getLogger(__name__)

price_agent = PriceAgent()


def run_root_workflow(portfolio_id=None, user_id=None):
    """Run the AI analysis workflow for all holdings (or a single portfolio / user).

    For each holding, fetches recent price data and computes metrics, then
    sends the batch to the OpenRouter AI service for analysis. Results are
    stored as alerts in the database.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if portfolio_id:
                cur.execute("SELECT * FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
            elif user_id:
                cur.execute("""
                    SELECT h.* FROM holdings h
                    JOIN portfolios p ON h.portfolio_id = p.id
                    WHERE p.user_id = %s
                """, (user_id,))
            else:
                cur.execute("SELECT * FROM holdings")
            holdings = cur.fetchall()

            if not holdings:
                logger.info("[Workflow] No holdings found to analyze")
                return True

            # Build holdings data for AI analysis
            holdings_data = []
            for h in holdings:
                ticker = h['ticker']
                buy_price = h.get('buy_price')
                last_price = h.get('last_price')

                # Compute P&L percentage if both prices available
                pnl_pct = None
                if buy_price and last_price and buy_price > 0:
                    pnl_pct = round(((last_price - buy_price) / buy_price) * 100, 2)

                holdings_data.append({
                    'ticker': ticker,
                    'shares': h['shares'],
                    'buy_price': buy_price,
                    'last_price': last_price,
                    'pnl_pct': pnl_pct,
                    'portfolio_id': h['portfolio_id'],
                })

            # Run AI analysis on the batch
            logger.info(f"[Workflow] Analyzing {len(holdings_data)} holdings via AI")
            analyses = analyze_holdings(holdings_data)

            # Map analyses back by ticker for lookup
            analysis_by_ticker = {a['ticker']: a for a in analyses}

            # Store alerts
            for h in holdings_data:
                ticker = h['ticker']
                analysis = analysis_by_ticker.get(ticker, {
                    'signal': 'NEUTRAL',
                    'risk_level': 'MEDIUM',
                    'summary': f'No analysis generated for {ticker}.',
                    'reasoning': '',
                })
                report = format_analysis_as_alert(ticker, analysis)
                cur.execute(
                    "INSERT INTO alerts (portfolio_id, message) VALUES (%s, %s)",
                    (h['portfolio_id'], report)
                )

            conn.commit()
            logger.info(f"[Workflow] Generated {len(holdings_data)} AI alerts")

    except Exception as e:
        conn.rollback()
        logger.error(f"[Workflow] Error: {e}")
    finally:
        put_db_connection(conn)

    return True

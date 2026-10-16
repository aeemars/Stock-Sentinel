import datetime
import yfinance as yf
import pandas as pd
import httpx
import os
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False

analyzer = SentimentIntensityAnalyzer()

class PriceAgent:
    """Fetch recent price history for tickers."""
    def fetch(self, ticker, period='7d', interval='1d'):
        df = yf.download(ticker, period=period, interval=interval, progress=False, multi_level_index=False)
        if df.empty:
            return None
        df = df.reset_index()[['Date','Close']].rename(columns={'Date':'ds','Close':'y'})
        return df

class SentimentAgent:
    """Search Farcaster casts via Neynar API and score sentiment with VADER."""
    def scrape(self, query, max_tweets=50):
        api_key = os.getenv('NEYNAR_API_KEY')
        if not api_key:
            return {'count':0, 'avg':0.0, 'scores':[]}
            
        try:
            url = "https://api.neynar.com/v2/farcaster/cast/search"
            params = {
                "q": query,
                "limit": max_tweets
            }
            headers = {
                "accept": "application/json",
                "api_key": api_key
            }
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
            
            texts = [cast.get('text', '') for cast in data.get('result', {}).get('casts', [])]
        except Exception as e:
            print(f"Error fetching from Neynar API: {e}")
            texts = []

        scores = [analyzer.polarity_scores(t)['compound'] for t in texts if t]
        if not scores:
            return {'count':0, 'avg':0.0, 'scores':[]}
        return {'count':len(scores), 'avg':float(sum(scores)/len(scores)), 'scores':scores}

class ForecastAgent:
    """Produce a short-term forecast using Prophet."""
    def forecast(self, df, periods=3):
        if not PROPHET_AVAILABLE:
            return None
        if df is None or df.empty:
            return None
        m = Prophet()
        m.fit(df)
        future = m.make_future_dataframe(periods=periods)
        fcst = m.predict(future)
        # Return only the forecast for the horizon
        return fcst[['ds','yhat','yhat_lower','yhat_upper']].tail(periods)

class SynthesizerAgent:
    """Meta-agent: combine outputs into a report string and simple risk flags."""
    def synthesize(self, ticker, price_df, sentiment, forecast_df):
        lines = []
        lines.append(f"Report for {ticker} - {datetime.date.today().isoformat()}")
        if price_df is not None and not price_df.empty:
            last = price_df.iloc[-1]['y']
            lines.append(f"Last close: {last:.2f}")
        if sentiment and sentiment['count']>0:
            lines.append(f"Social sentiment (avg): {sentiment['avg']:.3f} from {sentiment['count']} posts")
            if sentiment['avg']>0.6:
                lines.append("Hype risk: HIGH (very positive social buzz)")
        if forecast_df is not None and not forecast_df.empty:
            next_hat = forecast_df.iloc[ -1 ]['yhat']
            lines.append(f"Forecast {len(forecast_df)} days out: {next_hat:.2f}")
        return "\n".join(lines)

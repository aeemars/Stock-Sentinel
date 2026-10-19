import datetime
import yfinance as yf
import pandas as pd


class PriceAgent:
    """Fetch recent price history for tickers."""
    def fetch(self, ticker, period='7d', interval='1d'):
        df = yf.download(ticker, period=period, interval=interval, progress=False, multi_level_index=False)
        if df.empty:
            return None
        df = df.reset_index()[['Date','Close']].rename(columns={'Date':'ds','Close':'y'})
        return df

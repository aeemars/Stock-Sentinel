from roma.agents import PriceAgent
import pandas as pd

def test_price_fetch(monkeypatch):
    def fake_download(ticker, period='7d', interval='1d', progress=False, **kwargs):
        return pd.DataFrame({
            'Date': pd.to_datetime(['2026-05-28', '2026-05-29']),
            'Close': [100.0, 101.5],
        })

    monkeypatch.setattr('roma.agents.yf.download', fake_download)
    pa = PriceAgent()
    df = pa.fetch('AAPL', period='5d')
    assert df is not None
    assert 'y' in df.columns
    assert list(df.columns) == ['ds', 'y']

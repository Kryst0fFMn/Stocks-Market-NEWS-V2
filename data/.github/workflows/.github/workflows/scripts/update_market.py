import json
from datetime import datetime
from pathlib import Path

import feedparser
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "market.json"

tickers = {
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AAPL": "Apple",
    "AMZN": "Amazon",
    "TSLA": "Tesla",
}

stocks = []
for ticker, name in tickers.items():
    try:
        stock = yf.Ticker(ticker)
        info = stock.fast_info
        price = float(info.get("last_price") or info.get("lastPrice") or 0)
        prev_close = float(info.get("previous_close") or info.get("previousClose") or 1)
        change = ((price - prev_close) / prev_close) * 100 if prev_close else 0.0

        category = {
            "MSFT": "Cloud",
            "NVDA": "AI",
            "AAPL": "Hardware",
            "AMZN": "E-commerce",
            "TSLA": "EV",
        }.get(ticker, "Market")

        stocks.append({
            "name": name,
            "ticker": ticker,
            "price": price,
            "change": round(change, 2),
            "category": category
        })
    except Exception:
        continue

news_feed = feedparser.parse(
    "https://news.google.com/rss/search?q=Microsoft+OR+NVIDIA+OR+Apple+OR+Amazon+OR+Tesla&hl=cs&gl=CZ&ceid=CZ:cs"
)

news = []
for entry in news_feed.entries[:5]:
    title = entry.get("title", "Bez názvu")
    published = entry.get("published", "")
    news.append({
        "type": "alert" if "výpad" in title.lower() or "odchod" in title.lower() else ("down" if "pokles" in title.lower() else "up"),
        "time": published[:16] if published else "Nedávno",
        "text": title
    })

alerts = [
    {"title": "Výpadek cloudové infrastruktury", "time": "Před 14 min"},
    {"title": "Jensen Huang zůstává ve vedení", "time": "Před 51 min"},
    {"title": "Analytici upravují cílové ceny", "time": "Před 1 h 12 min"},
]

payload = {
    "stocks": stocks,
    "news": news,
    "alerts": alerts,
    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
}

DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

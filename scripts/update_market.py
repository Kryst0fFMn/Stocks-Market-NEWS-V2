import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "market.json"

# Yahoo Finance symboly pro různé burzy.
COMPANIES = [
    {"name": "Microsoft", "symbol": "MSFT", "market": "USA", "currency": "USD"},
    {"name": "NVIDIA", "symbol": "NVDA", "market": "USA", "currency": "USD"},
    {"name": "Apple", "symbol": "AAPL", "market": "USA", "currency": "USD"},
    {"name": "Amazon", "symbol": "AMZN", "market": "USA", "currency": "USD"},
    {"name": "HSBC", "symbol": "HSBA.L", "market": "Velká Británie", "currency": "GBp"},
    {"name": "AstraZeneca", "symbol": "AZN.L", "market": "Velká Británie", "currency": "GBp"},
    {"name": "SAP", "symbol": "SAP.DE", "market": "Německo", "currency": "EUR"},
    {"name": "Siemens", "symbol": "SIE.DE", "market": "Německo", "currency": "EUR"},
    {"name": "Toyota", "symbol": "7203.T", "market": "Japonsko", "currency": "JPY"},
    {"name": "Sony", "symbol": "6758.T", "market": "Japonsko", "currency": "JPY"},
    {"name": "Tencent", "symbol": "0700.HK", "market": "Hongkong", "currency": "HKD"},
    {"name": "Shopify", "symbol": "SHOP.TO", "market": "Kanada", "currency": "CAD"},
    {"name": "BHP", "symbol": "BHP.AX", "market": "Austrálie", "currency": "AUD"},
    {"name": "Reliance Industries", "symbol": "RELIANCE.NS", "market": "Indie", "currency": "INR"},
    {"name": "Nestlé", "symbol": "NESN.SW", "market": "Švýcarsko", "currency": "CHF"},
    {"name": "L'Oréal", "symbol": "OR.PA", "market": "Francie", "currency": "EUR"},
    {"name": "ASML", "symbol": "ASML.AS", "market": "Nizozemsko", "currency": "EUR"},
    {"name": "Petrobras", "symbol": "PETR4.SA", "market": "Brazílie", "currency": "BRL"},
]

ALIASES = {
    "Microsoft": ["microsoft", "msft"],
    "NVIDIA": ["nvidia", "nvda"],
    "Apple": ["apple", "aapl"],
    "Amazon": ["amazon", "amzn"],
    "HSBC": ["hsbc", "hsba"],
    "AstraZeneca": ["astrazeneca"],
    "SAP": ["sap"],
    "Siemens": ["siemens"],
    "Toyota": ["toyota"],
    "Sony": ["sony"],
    "Tencent": ["tencent"],
    "Shopify": ["shopify"],
    "BHP": ["bhp"],
    "Reliance Industries": ["reliance"],
    "Nestlé": ["nestle", "nestlé"],
    "L'Oréal": ["l'oreal", "l’oréal", "loreal"],
    "ASML": ["asml"],
    "Petrobras": ["petrobras"],
}


def estimate_impact(title):
    """Hrubé třídění podle slov v titulku, ne ověřená analýza."""
    text = title.lower()

    negative = (
        "outage", "výpad", "breach", "hack", "investigation", "investigat",
        "lawsuit", "žalob", "resign", "odstoup", "recall", "stahuje",
        "layoff", "propoušt", "downgrade", "pokles", "falls", "fall",
        "misses estimates", "sankc"
    )
    positive = (
        "beats estimates", "raises forecast", "raises guidance",
        "zvyšuje výhled", "růst zisku", "record profit", "rekordní zisk",
        "wins contract", "získala zakázku", "upgrade", "schválen"
    )

    if any(word in text for word in negative):
        return "Možný negativní signál: může zvýšit nejistotu, náklady nebo reputační riziko. Ověř zdroj."
    if any(word in text for word in positive):
        return "Možný pozitivní signál: může podpořit očekávání růstu nebo tržeb. Ověř zdroj."
    return "Dopad nelze spolehlivě určit pouze z titulku; otevři původní zprávu."


def load_quotes():
    symbols = [company["symbol"] for company in COMPANIES]
    frame = yf.download(
        tickers=symbols,
        period="5d",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )

    stocks = []
    for company in COMPANIES:
        symbol = company["symbol"]
        try:
            if isinstance(frame.columns, pd.MultiIndex):
                if symbol in frame.columns.get_level_values(0):
                    history = frame[symbol]
                elif symbol in frame.columns.get_level_values(-1):
                    history = frame.xs(symbol, axis=1, level=-1)
                else:
                    print(f"Bez dat: {symbol}")
                    continue
            else:
                history = frame

            closes = history["Close"].dropna()
            if len(closes) == 0:
                print(f"Bez ceny: {symbol}")
                continue

            latest = float(closes.iloc[-1])
            previous = float(closes.iloc[-2]) if len(closes) > 1 else latest
            change = ((latest - previous) / previous * 100) if previous else 0

            stocks.append({
                **company,
                "price": round(latest, 4),
                "change": round(change, 2),
            })
        except (KeyError, TypeError, ValueError, IndexError) as error:
            print(f"Přeskakuji {symbol}: {error}")

    if not stocks:
        raise RuntimeError("Yahoo Finance nevrátilo žádné kotace.")
    return stocks


def load_news():
    query = " OR ".join(f'"{name}"' for name in ALIASES)
    params = urllib.parse.urlencode({
        "q": query,
        "hl": "cs",
        "gl": "CZ",
        "ceid": "CZ:cs",
    })
    url = f"https://news.google.com/rss/search?{params}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MarketPulse/1.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())

    news = []
    seen = set()

    for item in root.findall("./channel/item"):
        title = item.findtext("title", default="Bez titulku").strip()
        link = item.findtext("link", default="")
        if not link or title in seen:
            continue
        seen.add(title)

        text = title.lower()
        company = "Více firem / neurčeno"
        for name, aliases in ALIASES.items():
            if any(alias in text for alias in aliases):
                company = name
                break

        published = item.findtext("pubDate", default="")
        try:
            date = parsedate_to_datetime(published)
            display_time = date.astimezone().strftime("%d.%m.%Y %H:%M")
        except (TypeError, ValueError):
            display_time = published or "Čas neuveden"

        news.append({
            "company": company,
            "title": title,
            "url": link,
            "time": display_time,
            "impact": estimate_impact(title),
        })

        if len(news) >= 20:
            break

    return news


def main():
    stocks = load_quotes()

    try:
        news = load_news()
    except Exception as error:
        print(f"Zprávy se nepodařilo načíst: {error}")
        news = []

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
        "news": news,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Uloženo {len(stocks)} kotací a {len(news)} zpráv.")


if __name__ == "__main__":
    main()

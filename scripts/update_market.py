import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "market.json"

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
    text = (title or "").lower()

    negative = (
        "outage", "výpad", "breach", "hack", "investigation", "investigat",
        "lawsuit", "žalob", "resign", "odstoup", "recall", "stahuje",
        "layoff", "propoušt", "downgrade", "pokles", "falls", "fall",
        "misses estimates", "sankc",
    )
    positive = (
        "beats estimates", "raises forecast", "raises guidance",
        "zvyšuje výhled", "růst zisku", "record profit", "rekordní zisk",
        "wins contract", "získala zakázku", "upgrade", "schválen",
    )

    if any(word in text for word in negative):
        return "Možný negativní signál; ověř zprávu u původního zdroje."
    if any(word in text for word in positive):
        return "Možný pozitivní signál; ověř zprávu u původního zdroje."
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
        threads=False,
    )

    if frame.empty:
        raise RuntimeError("Yahoo Finance nevrátilo žádné kotace.")

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
            if closes.empty:
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
        raise RuntimeError("Yahoo Finance nevrátilo žádné použitelné kotace.")

    return stocks


def fetch_rss(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; MarketPulse/1.0; "
                "+https://github.com/Kryst0fFMn/Stocks-Market-NEWS-V2)"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if not (error.code == 429 or 500 <= error.code < 600) or attempt == 2:
                raise
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise
            print(f"RSS požadavek se nepovedl, opakuji: {error}")
            time.sleep(2 ** attempt)
    return b""


def identify_company(title):
    text = (title or "").casefold()

    for name, aliases in ALIASES.items():
        for alias in aliases:
            alias = alias.casefold()
            if len(alias) <= 4 and alias.isascii() and alias.isalnum():
                if re.search(rf"\b{re.escape(alias)}\b", text):
                    return name
            elif alias in text:
                return name

    return "Více firem / neurčeno"


def parse_news_item(item):
    title = (item.findtext("title", default="") or "").strip()
    link = (item.findtext("link", default="") or "").strip()
    if not title or not link:
        return None

    published = (item.findtext("pubDate", default="") or "").strip()
    if published:
        try:
            dt = datetime.strptime(published, "%a, %d %b %Y %H:%M:%S %z")
            display_time = dt.astimezone().strftime("%d.%m.%Y %H:%M")
        except ValueError:
            display_time = published
    else:
        display_time = "Čas neuveden"

    source_element = item.find("source")
    source = (
        (source_element.text or "").strip()
        if source_element is not None and source_element.text
        else "Bing News"
    )

    return {
        "company": identify_company(title),
        "title": title,
        "url": link,
        "time": display_time,
        "impact": estimate_impact(title),
        "source": source,
    }


def fetch_yfinance_news(symbol, company_name):
    try:
        ticker = yf.Ticker(symbol)
        items = ticker.news or []
    except Exception as exc:
        print(f"Nešla načíst Yahoo news pro {symbol}: {exc}")
        return []

    news = []
    seen = set()

    for item in items[:6]:
        title = (item.get("title") or "").strip()
        link = (item.get("link") or item.get("url") or "").strip()
        if not title or not link:
            continue

        key = (title.casefold(), link)
        if key in seen:
            continue
        seen.add(key)

        ts = item.get("providerPublishTime") or item.get("publishedAt")
        if ts is not None:
            try:
                dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                display_time = dt.strftime("%d.%m.%Y %H:%M")
            except (TypeError, ValueError):
                display_time = "Čas neuveden"
        else:
            display_time = "Čas neuveden"

        news.append({
            "company": company_name,
            "title": title,
            "url": link,
            "time": display_time,
            "impact": estimate_impact(title),
            "source": item.get("publisher") or item.get("source") or "Yahoo Finance",
        })

    return news


def fetch_bing_news_batch(batch_names):
    batch = list(batch_names)
    query = " OR ".join(f'"{name}"' for name in batch)

    params = urllib.parse.urlencode({
        "q": query,
        "format": "RSS",
        "setmkt": "en-US",
    })
    url = f"https://www.bing.com/news/search?{params}"

    try:
        xml_data = fetch_rss(url)
        if not xml_data:
            return []

        root = ET.fromstring(xml_data)
        items = []
        for item in root.findall(".//item"):
            parsed = parse_news_item(item)
            if parsed:
                items.append(parsed)
        return items
    except Exception as exc:
        print(f"Nešel načíst Bing RSS pro {', '.join(batch)}: {exc}")
        return []


def load_news():
    news = []
    seen = set()

    # 1) primární zdroj: Yahoo Finance news
    for company in COMPANIES:
        for item in fetch_yfinance_news(company["symbol"], company["name"]):
            key = (item["title"].casefold(), item["url"])
            if key in seen:
                continue
            seen.add(key)
            news.append(item)

        # zkusíme krátkou pauzu kvůli rate limit
        time.sleep(0.4)

        if len(news) >= 20:
            return news[:20]

    # 2) fallback: Bing RSS (pokud yfinance nic nevrátí)
    if len(news) < 12:
        company_names = list(ALIASES.keys())
        batch_size = 6
        for start in range(0, len(company_names), batch_size):
            batch = company_names[start:start + batch_size]
            for item in fetch_bing_news_batch(batch):
                key = (item["title"].casefold(), item["url"])
                if key in seen:
                    continue
                seen.add(key)
                news.append(item)

            if len(news) >= 20:
                break

    return news[:20]


def main():
    stocks = load_quotes()
    news = load_news()

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

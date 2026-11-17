import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Prague")
except Exception:
    LOCAL_TZ = timezone.utc

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "market.json"

MAX_PER_SOURCE = 2     # kolik zpráv na firmu z jednoho zdroje
MAX_PER_COMPANY = 4    # kolik zpráv na firmu celkem
MAX_TOTAL = 24         # kolik zpráv se uloží celkem

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
        period="1y",
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
                # historie zavíracích cen pro graf: [["2026-01-02", 123.45], ...]
                "history": [
                    [day.strftime("%Y-%m-%d"), round(float(value), 2)]
                    for day, value in closes.items()
                ],
            })

        except (KeyError, TypeError, ValueError, IndexError) as error:
            print(f"Přeskakuji {symbol}: {error}")

    if not stocks:
        raise RuntimeError("Yahoo Finance nevrátilo žádné použitelné kotace.")

    return stocks


# ---------------------------------------------------------------- zprávy

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


def parse_any_date(value):
    """Zvládne RSS datum, ISO datum i unixový čas. Při chybě vrátí None."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)

        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)

        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def make_item(company, title, url, dt, source):
    return {
        "company": company,
        "title": title,
        "url": url,
        "time": dt.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M") if dt else "Čas neuveden",
        "impact": estimate_impact(title),
        "source": source,
        "_ts": dt.timestamp() if dt else 0,  # jen pro řazení, před uložením se maže
    }


def fetch_yfinance_news(symbol, company_name):
    try:
        items = yf.Ticker(symbol).news or []
    except Exception as exc:
        print(f"Nešla načíst Yahoo news pro {symbol}: {exc}")
        return []

    news = []

    for item in items:
        # Nový formát: všechno je v item["content"]. Starý: přímo v item.
        content = item.get("content") if isinstance(item.get("content"), dict) else item

        title = (content.get("title") or "").strip()

        url = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            value = content.get(key)
            if isinstance(value, dict):
                url = value.get("url") or ""
            elif isinstance(value, str):
                url = value
            if url:
                break
        url = (url or content.get("link") or content.get("url") or "").strip()

        if not title or not url:
            continue

        dt = parse_any_date(
            content.get("pubDate")
            or content.get("displayTime")
            or content.get("providerPublishTime")
            or content.get("publishedAt")
        )

        provider = content.get("provider")
        if isinstance(provider, dict):
            source = provider.get("displayName")
        else:
            source = content.get("publisher")

        news.append(make_item(company_name, title, url, dt, source or "Yahoo Finance"))

    print(f"yfinance {symbol}: {len(news)} zpráv (surových položek: {len(items)})")
    return news


def parse_rss(xml_data, company=None, default_source=""):
    root = ET.fromstring(xml_data)
    result = []

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue

        source_element = item.find("source")
        if source_element is not None and source_element.text:
            source = source_element.text.strip()
        else:
            source = default_source

        # Google News přidává na konec titulku " - Zdroj"
        suffix = f" - {source}"
        if source and title.endswith(suffix):
            title = title[:-len(suffix)].strip()

        dt = parse_any_date(item.findtext("pubDate"))
        result.append(make_item(company or identify_company(title), title, link, dt, source))

    return result


def fetch_google_news(company_name):
    params = urllib.parse.urlencode({
        "q": f'"{company_name}" when:3d',
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    url = f"https://news.google.com/rss/search?{params}"

    try:
        items = parse_rss(fetch_rss(url), company_name, "Google News")
    except Exception as exc:
        print(f"Google News pro {company_name} selhalo: {exc}")
        return []

    print(f"Google News {company_name}: {len(items)} zpráv")
    return items


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
        items = parse_rss(fetch_rss(url), None, "Bing News")
    except Exception as exc:
        print(f"Bing RSS pro {', '.join(batch)} selhalo: {exc}")
        return []

    print(f"Bing ({', '.join(batch)}): {len(items)} zpráv")
    return items


def add_unique(target, seen, per_company, items):
    for item in items:
        company = item["company"]
        if per_company.get(company, 0) >= MAX_PER_COMPANY:
            continue

        title_key = re.sub(r"\W+", " ", item["title"].casefold()).strip()
        if title_key in seen or item["url"] in seen:
            continue

        seen.add(title_key)
        seen.add(item["url"])
        per_company[company] = per_company.get(company, 0) + 1
        target.append(item)


def load_news():
    news = []
    seen = set()
    per_company = {}

    # 1) Yahoo Finance (zprávy ke konkrétnímu tickeru)
    for company in COMPANIES:
        items = fetch_yfinance_news(company["symbol"], company["name"])
        add_unique(news, seen, per_company, items[:MAX_PER_SOURCE])
        time.sleep(0.4)
    print(f"Po Yahoo Finance: {len(news)} zpráv")

    # 2) Google News RSS (aktuální události kolem firem)
    for company in COMPANIES:
        items = fetch_google_news(company["name"])
        add_unique(news, seen, per_company, items[:MAX_PER_SOURCE])
        time.sleep(0.4)
    print(f"Po Google News: {len(news)} zpráv")

    # 3) Bing RSS jen jako poslední záloha
    if len(news) < 12:
        names = list(ALIASES.keys())
        for start in range(0, len(names), 6):
            add_unique(news, seen, per_company, fetch_bing_news_batch(names[start:start + 6]))
        print(f"Po Bingu: {len(news)} zpráv")

    news.sort(key=lambda item: item["_ts"], reverse=True)
    news = news[:MAX_TOTAL]

    for item in news:
        item.pop("_ts", None)

    if not news:
        print("VAROVÁNÍ: žádný zdroj nevrátil zprávy.")

    return news


def main():
    stocks = load_quotes()
    news = load_news()

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
        "news": news,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # každý bod historie na jeden řádek, ať je soubor kratší a čitelný
    text = re.sub(
        r'\[\s*("\d{4}-\d{2}-\d{2}"),\s*(-?[\d.eE+-]+)\s*\]',
        r"[\1, \2]",
        text,
    )
    OUTPUT.write_text(text, encoding="utf-8")

    print(f"Uloženo {len(stocks)} kotací a {len(news)} zpráv.")


if __name__ == "__main__":
    main()

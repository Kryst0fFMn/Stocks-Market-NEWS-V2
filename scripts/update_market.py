import json
import os
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

MAX_PER_COMPANY = 3    # kolik zpráv na firmu (nejlepší z obou zdrojů)
MAX_TOTAL = 24         # kolik zpráv se uloží celkem
MAX_AGE_DAYS = 7       # starší zprávy se zahodí

# ---- Oznámení do telefonu (ntfy) ----
# Téma (topic) se bere z tajného nastavení GitHubu NTFY_TOPIC. Když chybí, oznámení se neposílají.
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
SITE_URL = "https://kryst0ffmn.github.io/Stocks-Market-NEWS-V2/"
PRICE_ALERT_STEP = 3.0         # upozornění při denní změně o 3 %, pak o 6 %, 9 %...
MAX_PRICE_ALERTS = 5           # kolik cenových oznámení nejvýš za jeden běh
MAX_NEWS_ALERTS = 5            # kolik oznámení o zprávách nejvýš za jeden běh
NOTIFY_NEUTRAL_NEWS = False    # True = oznamovat i zprávy bez rozpoznatelného dopadu

# "query" (nepovinné) = jak firmu hledat na Google News, když by samotné jméno bylo nejednoznačné
COMPANIES = [
    {"name": "Microsoft", "symbol": "MSFT", "market": "USA", "currency": "USD"},
    {"name": "NVIDIA", "symbol": "NVDA", "market": "USA", "currency": "USD"},
    {"name": "Apple", "symbol": "AAPL", "market": "USA", "currency": "USD", "query": '"Apple Inc"'},
    {"name": "Amazon", "symbol": "AMZN", "market": "USA", "currency": "USD", "query": '(Amazon OR AWS OR AMZN)'},
    {"name": "Alphabet", "symbol": "GOOGL", "market": "USA", "currency": "USD", "query": "(Alphabet OR Google)"},
    {"name": "Meta", "symbol": "META", "market": "USA", "currency": "USD", "query": '"Meta Platforms"'},
    {"name": "Tesla", "symbol": "TSLA", "market": "USA", "currency": "USD"},
    {"name": "SpaceX", "symbol": "SPCX", "market": "USA", "currency": "USD"},
    {"name": "Berkshire Hathaway", "symbol": "BRK-B", "market": "USA", "currency": "USD"},
    {"name": "JPMorgan", "symbol": "JPM", "market": "USA", "currency": "USD", "query": "(JPMorgan OR Dimon)"},
    {"name": "Visa", "symbol": "V", "market": "USA", "currency": "USD", "query": '"Visa Inc"'},
    {"name": "Eli Lilly", "symbol": "LLY", "market": "USA", "currency": "USD", "query": '"Eli Lilly"'},
    {"name": "Broadcom", "symbol": "AVGO", "market": "USA", "currency": "USD"},
    {"name": "TSMC", "symbol": "2330.TW", "market": "Tchaj-wan", "currency": "TWD", "query": '(TSMC OR "Taiwan Semiconductor")'},
    {"name": "ASML", "symbol": "ASML.AS", "market": "Nizozemsko", "currency": "EUR"},
    {"name": "SAP", "symbol": "SAP.DE", "market": "Německo", "currency": "EUR", "query": "SAP SE"},
    {"name": "LVMH", "symbol": "MC.PA", "market": "Francie", "currency": "EUR"},
    {"name": "AstraZeneca", "symbol": "AZN.L", "market": "Velká Británie", "currency": "GBp"},
    {"name": "Toyota", "symbol": "7203.T", "market": "Japonsko", "currency": "JPY"},
    {"name": "Tencent", "symbol": "0700.HK", "market": "Hongkong", "currency": "HKD"},
]

# Co hledáme na Google News: jen zprávy s investičním významem
INVEST_QUERY = (
    "(earnings OR stock OR shares OR analyst OR guidance OR forecast OR lawsuit OR "
    "regulator OR acquisition OR deal OR outage OR CEO OR layoffs OR tariff)"
)

# Titulek musí obsahovat aspoň jedno z těchto slov, aby šel mezi zprávy
INVEST_WORDS = (
    "earnings", "revenue", "profit", "guidance", "forecast", "outlook", "analyst",
    "price target", "upgrade", "downgrade", "stock", "shares", "investor", "dividend",
    "buyback", "repurchase", "acquir", "acquisition", "merger", "takeover", "deal",
    "contract", "partnership", "lawsuit", "sued", "sues", "antitrust", "investigation",
    "probe", "fined", "penalty", "regulator", "regulation", "tariff", "sanction",
    "export", "ban", "outage", "breach", "hack", "recall", "layoff", "job cuts",
    "ceo", "cfo", "resign", "steps down", "ipo", "valuation", "market cap", "quarter",
    "sales", "demand", "approval", "fda", "stake", "lock-up", "lockup", "record",
    "plunge", "surge", "soar", "tumble", "sell-off", "selloff", "rally",
)

# Titulky, které s investováním nesouvisí (slevy, návody, recenze...)
NOISE_WORDS = (
    "best deals", "black friday", "cyber monday", "coupon", "promo code", "% off",
    "gift guide", "how to ", "tips and tricks", "review:", "hands-on", "unboxing",
    "wallpaper", "trailer", "wordle", "crossword", "horoscope", "recipe",
    "giveaway", "podcast", "opinion:",
)

# (směr, klíčová slova v titulku, důvod). Směr: up / down / mixed.
IMPACT_RULES = [
    ("up", ("beats estimates", "beat estimates", "tops estimates", "topped estimates",
            "better-than-expected", "better than expected", "record revenue", "record profit",
            "record earnings", "profit jumps", "revenue jumps", "strong earnings",
            "strong results", "raises guidance", "raises forecast", "raises outlook",
            "boosts outlook", "lifts forecast", "lifts guidance"),
     "silnější výsledky nebo vyšší výhled zvyšují očekávání budoucích zisků"),
    ("up", ("upgraded to", "upgrades to", "upgrade to", "price target raised",
            "raises price target", "raised its price target", "lifts price target",
            "boosts price target", "initiates coverage with buy", "buy rating",
            "overweight rating", "outperform rating"),
     "analytici zlepšují hodnocení, což často přitáhne nové kupce"),
    ("up", ("buyback", "share repurchase", "raises dividend", "dividend increase",
            "special dividend"),
     "zpětný odkup akcií nebo vyšší dividenda zvyšují hodnotu pro akcionáře"),
    ("up", ("wins contract", "awarded contract", "lands contract", "signs deal",
            "signs agreement", "multibillion", "multi-billion", "strikes deal",
            "expands partnership", "new partnership", "strategic partnership"),
     "nový kontrakt nebo partnerství může zvýšit budoucí tržby"),
    ("up", ("fda approves", "receives approval", "record orders", "demand surge",
            "strong demand", "record deliveries", "record sales"),
     "schválení nebo silná poptávka mohou zvýšit tržby"),
    ("up", ("surges", "soars", "jumps", "rallies", "all-time high", "record high"),
     "prudký růst ukazuje na silný zájem investorů, po rychlém růstu ale může přijít i stahování zisků"),

    ("down", ("misses estimates", "missed estimates", "miss estimates", "below expectations",
              "worse-than-expected", "worse than expected", "falls short", "profit falls",
              "profit drops", "revenue falls", "revenue drops", "weak earnings", "weak results",
              "cuts guidance", "cuts forecast", "lowers outlook", "lowers guidance",
              "profit warning", "slashes forecast"),
     "slabší výsledky nebo snížený výhled snižují očekávání zisků"),
    ("down", ("downgraded", "downgrades", "downgrade to", "cuts price target",
              "lowers price target", "price target cut", "sell rating", "underweight rating",
              "underperform rating"),
     "analytici zhoršují hodnocení, což může zvýšit prodejní tlak"),
    ("down", ("lawsuit", "sued", "sues", "antitrust", "investigation", "probe", "fined",
              "penalty", "penalties", "banned", "sanctions", "tariff", "export controls",
              "export curbs", "export restrictions", "subpoena", "class action"),
     "právní nebo regulační riziko může znamenat pokuty či omezení podnikání"),
    ("down", ("outage", "breach", "hacked", "cyberattack", "data leak", "ransomware",
              "security flaw"),
     "výpadek nebo bezpečnostní incident poškozuje důvěru zákazníků a může znamenat ztráty i pokuty"),
    ("down", ("recall", "recalls", "safety probe", "halts production", "halts sales",
              "suspends", "grounded"),
     "stažení výrobků nebo zastavení výroby znamená ztrátu tržeb a další náklady"),
    ("down", ("steps down", "resigns", "resignation", "departs", "departure",
              "leaves company", "exits as ceo", "ousted", "fired as ceo", "to leave"),
     "odchod klíčového manažera zvyšuje nejistotu o dalším směřování firmy"),
    ("down", ("lock-up", "lockup", "sells stake", "stake sale", "insider selling", "share sale"),
     "prodej akcií nebo konec lock-upu zvyšuje nabídku akcií na trhu"),
    ("down", ("plunges", "plunge", "tumbles", "sinks", "slumps", "sell-off", "selloff", "slides"),
     "prudký pokles ukazuje na silný prodejní tlak a nervozitu investorů"),

    ("mixed", ("layoffs", "job cuts", "cuts jobs", "lays off", "laying off", "workforce reduction"),
     "propouštění může ušetřit náklady, ale často signalizuje slabší poptávku"),
    ("mixed", ("acquires", "to acquire", "acquisition", "merger", "takeover bid"),
     "akvizice může firmu posílit, ale znamená i vysokou cenu a integrační riziko"),
]


def analyze_impact(title, company):
    """Jednoduchý odhad podle klíčových slov v titulku. Není to investiční rada."""
    text = (title or "").casefold()
    matched = [(d, reason) for d, words, reason in IMPACT_RULES if any(w in text for w in words)]

    if not matched:
        return "neutral", (
            f"Dopad na cenu akcie {company} nelze z titulku spolehlivě určit; "
            "otevři původní zprávu."
        )

    decisive = [m for m in matched if m[0] != "mixed"]
    if {"up", "down"} <= {d for d, _ in decisive}:
        return "mixed", (
            f"Zpráva obsahuje pozitivní i negativní signály, dopad na cenu akcie {company} je nejasný."
        )

    direction, reason = (decisive or matched)[0]
    if direction == "up":
        return "up", f"Může vést k nárůstu ceny akcie {company}: {reason}."
    if direction == "down":
        return "down", f"Může vést k poklesu ceny akcie {company}: {reason}."
    return "mixed", f"Dopad na cenu akcie {company} je nejednoznačný: {reason}."


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


def load_intraday():
    """15minutové ceny za posledních 5 obchodních dní (pro grafy 1D a 1W)."""
    symbols = [company["symbol"] for company in COMPANIES]

    try:
        frame = yf.download(
            tickers=symbols,
            period="5d",
            interval="15m",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as exc:
        print(f"Minutová data se nepodařilo stáhnout: {exc}")
        return {}

    if frame is None or frame.empty:
        print("Minutová data: Yahoo Finance nic nevrátilo.")
        return {}

    result = {}

    for symbol in symbols:
        try:
            if isinstance(frame.columns, pd.MultiIndex):
                if symbol in frame.columns.get_level_values(0):
                    history = frame[symbol]
                elif symbol in frame.columns.get_level_values(-1):
                    history = frame.xs(symbol, axis=1, level=-1)
                else:
                    continue
            else:
                history = frame

            closes = history["Close"].dropna()
            if closes.empty:
                continue

            index = closes.index
            index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")

            result[symbol] = [
                [stamp.strftime("%Y-%m-%dT%H:%MZ"), round(float(value), 2)]
                for stamp, value in zip(index, closes.values)
            ]
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            print(f"Minutová data {symbol} přeskočena: {error}")

    print(f"Minutová data: {len(result)} z {len(symbols)} akcií")
    return result


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


def make_item(company_name, title, url, dt, source):
    direction, impact = analyze_impact(title, company_name)
    return {
        "company": company_name,
        "title": title,
        "url": url,
        "time": dt.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M") if dt else "Čas neuveden",
        "direction": direction,
        "impact": impact,
        "source": source,
        "_ts": dt.timestamp() if dt else 0,  # jen pro řazení, před uložením se maže
    }


def fetch_yfinance_news(company):
    try:
        items = yf.Ticker(company["symbol"]).news or []
    except Exception as exc:
        print(f"Nešla načíst Yahoo news pro {company['symbol']}: {exc}")
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
        source = provider.get("displayName") if isinstance(provider, dict) else content.get("publisher")

        news.append(make_item(company["name"], title, url, dt, source or "Yahoo Finance"))

    return news


def fetch_google_news(company):
    base = company.get("query") or f'"{company["name"]}"'
    params = urllib.parse.urlencode({
        "q": f"{base} {INVEST_QUERY} when:3d",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    url = f"https://news.google.com/rss/search?{params}"

    try:
        root = ET.fromstring(fetch_rss(url))
    except Exception as exc:
        print(f"Google News pro {company['name']} selhalo: {exc}")
        return []

    result = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue

        source_element = item.find("source")
        source = (source_element.text or "").strip() if source_element is not None and source_element.text else "Google News"

        # Google News přidává na konec titulku " - Zdroj"
        suffix = f" - {source}"
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()

        result.append(make_item(company["name"], title, link, parse_any_date(item.findtext("pubDate")), source))

    return result


def is_investing_news(title):
    text = (title or "").casefold()
    if any(word in text for word in NOISE_WORDS):
        return False
    return any(word in text for word in INVEST_WORDS)


def collect_candidates(company):
    """Zprávy jedné firmy: jen čerstvé a investiční, nejlepší první."""
    raw = fetch_yfinance_news(company) + fetch_google_news(company)
    oldest = datetime.now(timezone.utc).timestamp() - MAX_AGE_DAYS * 86400

    good = [
        item for item in raw
        if is_investing_news(item["title"]) and (item["_ts"] == 0 or item["_ts"] >= oldest)
    ]
    # zprávy s rozpoznatelným dopadem dopředu, uvnitř skupiny nejnovější první
    good.sort(key=lambda item: (item["direction"] != "neutral", item["_ts"]), reverse=True)

    print(f"Zprávy {company['name']}: {len(raw)} nalezeno, {len(good)} po filtru")
    return good


def load_news():
    news = []
    seen = set()

    for company in COMPANIES:
        taken = 0
        for item in collect_candidates(company):
            title_key = re.sub(r"\W+", " ", item["title"].casefold()).strip()
            if title_key in seen or item["url"] in seen:
                continue
            seen.add(title_key)
            seen.add(item["url"])
            news.append(item)
            taken += 1
            if taken >= MAX_PER_COMPANY:
                break
        time.sleep(0.4)

    # do výběru jdou nejdřív zprávy s jasným dopadem, pak nejnovější; zobrazí se od nejnovější
    news.sort(key=lambda item: (item["direction"] != "neutral", item["_ts"]), reverse=True)
    news = sorted(news[:MAX_TOTAL], key=lambda item: item["_ts"], reverse=True)

    for item in news:
        item.pop("_ts", None)

    if not news:
        print("VAROVÁNÍ: žádný zdroj nevrátil použitelné zprávy.")

    return news


# ---------------------------------------------------------------- oznámení

def load_previous():
    """Předchozí uložená data (pro zjištění, co je nové). Při chybě prázdný slovník."""
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def send_ntfy(title, message, click=None, tags=None, priority=3):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False

    payload = {"topic": topic, "title": title, "message": message, "priority": priority}
    if click:
        payload["click"] = click
    if tags:
        payload["tags"] = tags

    request = urllib.request.Request(
        NTFY_SERVER,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "MarketPulse/1.0"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception as exc:  # oznámení nikdy nesmí shodit celý běh
        print(f"Oznámení se nepodařilo odeslat: {exc}")
        return False


def fmt_number(value, decimals=2):
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def fmt_price(stock):
    price = stock.get("price")
    currency = stock.get("currency") or ""
    if not isinstance(price, (int, float)):
        return ""
    if currency == "GBp":  # Yahoo uvádí britské akcie v pencích
        return f"{fmt_number(price / 100)} GBP"
    return f"{fmt_number(price)} {currency}".strip()


def title_key(title):
    return re.sub(r"\W+", " ", (title or "").casefold()).strip()


NEWS_LABELS = {
    "up": ("▲", "možný růst", "chart_with_upwards_trend"),
    "down": ("▼", "možný pokles", "chart_with_downwards_trend"),
    "mixed": ("◆", "nejasný dopad", "warning"),
    "neutral": ("●", "bez jasného dopadu", "newspaper"),
}


def notify_new_news(previous, news):
    old = previous.get("news")
    if not isinstance(old, list) or not old:
        print("Oznámení o zprávách: nemám s čím porovnat (první běh), přeskakuji.")
        return

    known = set()
    for item in old:
        if isinstance(item, dict):
            known.add(item.get("url"))
            known.add(title_key(item.get("title")))

    fresh = [
        item for item in news
        if item["url"] not in known and title_key(item["title"]) not in known
        and (NOTIFY_NEUTRAL_NEWS or item.get("direction") in ("up", "down", "mixed"))
    ]
    # zprávy s jasným dopadem dopředu (uvnitř skupiny zůstává pořadí od nejnovější)
    fresh.sort(key=lambda item: item.get("direction") == "neutral")

    sent = 0
    for item in fresh[:MAX_NEWS_ALERTS]:
        arrow, label, tag = NEWS_LABELS.get(item.get("direction"), NEWS_LABELS["neutral"])
        if send_ntfy(
            f"{arrow} {item['company']}: {label}",
            f"{item['title']}\n{item.get('impact', '')}".strip(),
            click=item["url"],
            tags=[tag],
        ):
            sent += 1

    rest = len(fresh) - MAX_NEWS_ALERTS
    if rest > 0:
        send_ntfy("Další nové zprávy", f"Na webu je ještě {rest} dalších nových zpráv.", click=SITE_URL, tags=["newspaper"])

    print(f"Oznámení o zprávách: {len(fresh)} nových, odesláno {sent}.")


def alert_level(change):
    return int(abs(change) // PRICE_ALERT_STEP)


def notify_price_moves(previous, stocks):
    old_stocks = previous.get("stocks")
    if not isinstance(old_stocks, list) or not old_stocks:
        print("Cenová oznámení: nemám s čím porovnat (první běh), přeskakuji.")
        return

    old = {item.get("symbol"): item for item in old_stocks if isinstance(item, dict)}
    alerts = []

    for stock in stocks:
        change = stock.get("change")
        if not isinstance(change, (int, float)) or alert_level(change) < 1:
            continue

        before = old.get(stock["symbol"], {}).get("change")
        before_level = alert_level(before) if isinstance(before, (int, float)) else 0
        same_direction = isinstance(before, (int, float)) and (before >= 0) == (change >= 0)

        # oznámit, když je to první překročení, další úroveň, nebo obrat směru
        if before_level == 0 or not same_direction or alert_level(change) > before_level:
            alerts.append(stock)

    alerts.sort(key=lambda stock: abs(stock["change"]), reverse=True)

    sent = 0
    for stock in alerts[:MAX_PRICE_ALERTS]:
        up = stock["change"] > 0
        if send_ntfy(
            f"{'▲' if up else '▼'} {stock['name']}: {stock['change']:+.1f} %".replace(".", ","),
            f"{fmt_price(stock)} · změna oproti předchozímu zavření",
            click=SITE_URL,
            tags=["chart_with_upwards_trend" if up else "chart_with_downwards_trend"],
            priority=4,
        ):
            sent += 1

    rest = len(alerts) - MAX_PRICE_ALERTS
    if rest > 0:
        send_ntfy("Další velké pohyby cen", f"Ještě {rest} dalších akcií se pohnulo o víc než {PRICE_ALERT_STEP:.0f} %.", click=SITE_URL)

    print(f"Cenová oznámení: {len(alerts)} k odeslání, odesláno {sent}.")


def main():
    previous = load_previous()
    stocks = load_quotes()

    # minutová data pro grafy 1D a 1W; když selžou, zbytek stránky funguje dál
    intraday = load_intraday()
    for stock in stocks:
        stock["intraday"] = intraday.get(stock["symbol"], [])

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
        r'\[\s*("\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}Z)?"),\s*(-?[\d.eE+-]+)\s*\]',
        r"[\1, \2]",
        text,
    )
    OUTPUT.write_text(text, encoding="utf-8")

    print(f"Uloženo {len(stocks)} kotací a {len(news)} zpráv.")

    # oznámení do telefonu (až po uložení dat; chyba oznámení běh neshodí)
    try:
        if os.environ.get("NTFY_TEST", "").strip().lower() == "true":
            send_ntfy("Market Pulse – test", "Oznámení fungují ✅", click=SITE_URL, tags=["white_check_mark"])
        notify_new_news(previous, news)
        notify_price_moves(previous, stocks)
    except Exception as exc:
        print(f"Oznámení přeskočena kvůli chybě: {exc}")


if __name__ == "__main__":
    main()

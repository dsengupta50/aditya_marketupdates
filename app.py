from flask import Flask, render_template, request, jsonify
import yfinance as yf
from duckduckgo_search import DDGS
import requests
from datetime import datetime, timezone

app = Flask(__name__)

MARKET_ASSETS = [
    {"name": "Dow Jones",        "ticker": "^DJI",  "label": "DOW",            "stooq": "^dji"},
    {"name": "S&P 500",          "ticker": "^GSPC", "label": "S&P 500",        "stooq": "^spx"},
    {"name": "NASDAQ",           "ticker": "^IXIC", "label": "NASDAQ",         "stooq": "^ndq"},
    {"name": "Gold",             "ticker": "GC=F",  "label": "Gold",           "stooq": "xauusd"},
    {"name": "10-Year Treasury", "ticker": "^TNX",  "label": "10-Yr Treasury", "stooq": "10us.b"},
]

# In-memory cache: ticker -> last successful result dict
_market_cache: dict = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fetch_yfinance(asset: dict) -> dict | None:
    """Fetch intraday data from yfinance. Returns result dict or None on failure."""
    for period, interval in [("1d", "5m"), ("5d", "15m")]:
        try:
            hist = yf.Ticker(asset["ticker"]).history(period=period, interval=interval)
            if hist.empty:
                continue

            closes = hist["Close"].dropna()
            if closes.empty:
                continue

            timestamps = [t.strftime("%H:%M") for t in closes.index]
            prices     = [round(float(p), 4) for p in closes.values]
            latest     = prices[-1]
            opens      = hist["Open"].dropna()
            open_price = float(opens.iloc[0]) if not opens.empty else None
            pct_change = round(((latest - open_price) / open_price) * 100, 2) if open_price else None

            return {
                "name":       asset["name"],
                "label":      asset["label"],
                "ticker":     asset["ticker"],
                "latest":     latest,
                "pct_change": pct_change,
                "timestamps": timestamps,
                "prices":     prices,
                "source":     "live",
                "fetched_at": _now_iso(),
                "stale":      False,
            }
        except Exception:
            continue
    return None


def _fetch_stooq(asset: dict) -> dict | None:
    """Fetch a spot quote from the free Stooq CSV API. Returns result dict or None."""
    stooq_sym = asset.get("stooq")
    if not stooq_sym:
        return None
    try:
        url  = f"https://stooq.com/q/l/?s={stooq_sym}&f=sd2t2ohlcv&h&e=csv"
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        lines = resp.text.strip().splitlines()
        if len(lines) < 2:
            return None
        # Header: Symbol,Date,Time,Open,High,Low,Close,Volume
        parts = lines[1].split(",")
        if len(parts) < 7 or parts[6] in ("", "N/D"):
            return None
        close      = float(parts[6])
        open_price = float(parts[3]) if parts[3] not in ("", "N/D") else None
        pct_change = round(((close - open_price) / open_price) * 100, 2) if open_price else None

        return {
            "name":       asset["name"],
            "label":      asset["label"],
            "ticker":     asset["ticker"],
            "latest":     round(close, 4),
            "pct_change": pct_change,
            "timestamps": [],   # no intraday series from spot quote
            "prices":     [],
            "source":     "stooq",
            "fetched_at": _now_iso(),
            "stale":      False,
        }
    except Exception:
        return None


def _fetch_asset(asset: dict) -> dict:
    """Try all sources in order; fall back to cache; return error dict as last resort."""
    # Source 1 & 2: yfinance (intraday, then extended)
    result = _fetch_yfinance(asset)
    if result:
        _market_cache[asset["ticker"]] = result
        return result

    # Source 3: Stooq spot quote
    result = _fetch_stooq(asset)
    if result:
        _market_cache[asset["ticker"]] = result
        return result

    # Source 4: last known cached data
    cached = _market_cache.get(asset["ticker"])
    if cached:
        stale = dict(cached)
        stale["stale"]  = True
        stale["source"] = "cache"
        return stale

    # Nothing available
    return {
        "name":   asset["name"],
        "label":  asset["label"],
        "ticker": asset["ticker"],
        "error":  "No data available from any source",
        "source": "none",
        "stale":  True,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/news", methods=["POST"])
def get_news():
    topic = request.json.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "Please enter a topic."}), 400

    results = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.news(topic, max_results=5):
                results.append({
                    "title":  item.get("title", "No title"),
                    "body":   item.get("body", ""),
                    "url":    item.get("url", "#"),
                    "source": item.get("source", ""),
                    "date":   item.get("date", ""),
                })
    except Exception as e:
        return jsonify({"error": f"Could not fetch news: {str(e)}"}), 500

    if not results:
        return jsonify({"error": "No news found for this topic."}), 404

    return jsonify({"articles": results})


@app.route("/api/market", methods=["GET"])
def get_market():
    data = [_fetch_asset(asset) for asset in MARKET_ASSETS]
    return jsonify({"assets": data})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

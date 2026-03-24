from flask import Flask, render_template, request, jsonify
import yfinance as yf
from duckduckgo_search import DDGS
import requests
from datetime import datetime, timezone
import os
import re
import collections

app = Flask(__name__)

# ── Trusted business / finance news source domains ───────────────────────────
BUSINESS_SOURCE_DOMAINS = {
    'wsj.com', 'ft.com', 'bloomberg.com', 'reuters.com',
    'cnbc.com', 'marketwatch.com', 'nytimes.com', 'barrons.com',
    'economist.com', 'businessinsider.com', 'fortune.com', 'forbes.com',
    'seekingalpha.com', 'thestreet.com', 'morningstar.com',
    'investing.com', 'nasdaq.com', 'apnews.com', 'axios.com',
    'financialtimes.com', 'yahoofinance.com', 'finance.yahoo.com',
}

STOPWORDS = {
    'a','about','above','after','again','against','all','am','an','and','any',
    'are','as','at','be','because','been','before','being','below','between',
    'both','but','by','can','could','did','do','does','doing','down','during',
    'each','few','for','from','get','got','had','has','have','having','he','her',
    'here','hers','him','his','how','if','in','into','is','it','its','itself',
    'just','let','me','more','most','my','no','nor','not','now','of','off','on',
    'once','only','or','other','our','out','over','own','said','same','she','so',
    'some','such','than','that','the','their','them','then','there','these',
    'they','this','those','through','to','too','under','until','up','very','was',
    'we','were','what','when','where','which','while','who','whom','why','will',
    'with','would','you','your','yours','also','says','year','new','one','two',
    'three','may','last','next','first','second','third',
}

MARKET_ASSETS = [
    {"name": "Dow Jones",        "ticker": "^DJI",  "label": "DOW",            "stooq": "^dji"},
    {"name": "S&P 500",          "ticker": "^GSPC", "label": "S&P 500",        "stooq": "^spx"},
    {"name": "NASDAQ",           "ticker": "^IXIC", "label": "NASDAQ",         "stooq": "^ndq"},
    {"name": "Gold",             "ticker": "GC=F",  "label": "Gold",           "stooq": "xauusd"},
    {"name": "10-Year Treasury", "ticker": "^TNX",  "label": "10-Yr Treasury", "stooq": "10us.b"},
]

_market_cache: dict = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Market data fetchers (unchanged) ─────────────────────────────────────────

def _fetch_yfinance(asset: dict) -> dict | None:
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
                "name": asset["name"], "label": asset["label"], "ticker": asset["ticker"],
                "latest": latest, "pct_change": pct_change,
                "timestamps": timestamps, "prices": prices,
                "source": "live", "fetched_at": _now_iso(), "stale": False,
            }
        except Exception:
            continue
    return None


def _fetch_stooq(asset: dict) -> dict | None:
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
        parts = lines[1].split(",")
        if len(parts) < 7 or parts[6] in ("", "N/D"):
            return None
        close      = float(parts[6])
        open_price = float(parts[3]) if parts[3] not in ("", "N/D") else None
        pct_change = round(((close - open_price) / open_price) * 100, 2) if open_price else None
        return {
            "name": asset["name"], "label": asset["label"], "ticker": asset["ticker"],
            "latest": round(close, 4), "pct_change": pct_change,
            "timestamps": [], "prices": [],
            "source": "stooq", "fetched_at": _now_iso(), "stale": False,
        }
    except Exception:
        return None


def _fetch_asset(asset: dict) -> dict:
    result = _fetch_yfinance(asset)
    if result:
        _market_cache[asset["ticker"]] = result
        return result
    result = _fetch_stooq(asset)
    if result:
        _market_cache[asset["ticker"]] = result
        return result
    cached = _market_cache.get(asset["ticker"])
    if cached:
        stale = dict(cached)
        stale["stale"]  = True
        stale["source"] = "cache"
        return stale
    return {
        "name": asset["name"], "label": asset["label"], "ticker": asset["ticker"],
        "error": "No data available from any source", "source": "none", "stale": True,
    }


# ── News helpers ──────────────────────────────────────────────────────────────

def _is_business_source(url: str) -> bool:
    """Return True if the URL belongs to a known business/finance news outlet."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().replace('www.', '')
        return any(domain == s or domain.endswith('.' + s) for s in BUSINESS_SOURCE_DOMAINS)
    except Exception:
        return False


def _prioritize_news(articles: list, limit: int = 5) -> list:
    """Return up to `limit` articles with business sources ranked first."""
    business = [a for a in articles if _is_business_source(a.get('url', ''))]
    other    = [a for a in articles if not _is_business_source(a.get('url', ''))]
    return (business + other)[:limit]


# ── Trend compilation ─────────────────────────────────────────────────────────

def _extract_keywords(text: str, top_n: int = 10) -> list:
    words = re.findall(r'\b[a-z]{4,}\b', text.lower())
    freq  = collections.Counter(w for w in words if w not in STOPWORDS)
    return [w for w, _ in freq.most_common(top_n)]


def _compile_trends_claude(topic: str, articles: list) -> list | None:
    """Use Claude API (Haiku) to synthesise 3 trend summaries from recent articles.
    Returns None if the API key is absent or the call fails."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic, json

        snippets = []
        for i, a in enumerate(articles[:12]):
            snippets.append(
                f"[{i+1}] {a.get('title', '')}  "
                f"({a.get('source', '')} · {str(a.get('date', ''))[:10]})\n"
                f"    {a.get('body', '')[:280]}"
            )

        prompt = (
            f'You are a senior financial analyst. Based on the following recent news articles '
            f'about "{topic}", identify exactly 3 distinct, non-overlapping trends and synthesise each one.\n\n'
            + "\n\n".join(snippets) +
            '\n\nRespond with a JSON array of exactly 3 objects, each with:\n'
            '  "title"   – concise trend headline (≤12 words)\n'
            '  "summary" – 2-3 sentence synthesis drawn from multiple articles (not a single article)\n'
            '  "cited"   – array of 1-based article index numbers supporting this trend\n'
            'Respond ONLY with valid JSON, no markdown fences.'
        )

        client      = anthropic.Anthropic(api_key=api_key)
        msg         = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r'^```[a-z]*\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        trends_data = json.loads(raw)

        result = []
        for t in trends_data[:3]:
            cited = t.get("cited", [])
            url, source, date = '#', '', ''
            if cited:
                idx = int(cited[0]) - 1
                if 0 <= idx < len(articles):
                    url    = articles[idx].get('url', '#')
                    source = articles[idx].get('source', '')
                    date   = articles[idx].get('date', '')
            result.append({
                "title":       t.get("title", "Trend"),
                "body":        t.get("summary", ""),
                "url":         url,
                "source":      source,
                "date":        date,
                "ai_compiled": True,
            })
        return result
    except Exception:
        return None


def _compile_trends_simple(topic: str, articles: list) -> list:
    """Fallback: cluster articles into 3 thematic groups using keyword overlap."""
    if not articles:
        return []

    scored = []
    for a in articles:
        kws = set(_extract_keywords(a.get('title', '') + ' ' + a.get('body', ''), top_n=12))
        scored.append((a, kws))

    # Pick 3 seed articles that are maximally distinct from each other
    seeds = [scored[0]]
    for a, kws in scored[1:]:
        if len(seeds) >= 3:
            break
        if all(len(kws & sk) < 3 for _, sk in seeds):
            seeds.append((a, kws))
    i = 0
    while len(seeds) < 3 and i < len(scored):
        if scored[i] not in seeds:
            seeds.append(scored[i])
        i += 1

    # Assign each article to its closest seed cluster
    clusters: list[list] = [[] for _ in seeds]
    for a, kws in scored:
        overlaps = [len(kws & sk) for _, sk in seeds]
        clusters[overlaps.index(max(overlaps))].append(a)

    trends = []
    for cluster in clusters:
        if not cluster:
            continue
        lead = cluster[0]
        # Combine body sentences from top articles in the cluster
        all_sents: list[str] = []
        for art in cluster[:4]:
            sents = re.split(r'(?<=[.!?])\s+', art.get('body', ''))
            all_sents += [s.strip() for s in sents if len(s.strip()) > 50]
        summary = ' '.join(all_sents[:3])[:500]
        sources = ', '.join({a.get('source', '') for a in cluster[:3] if a.get('source')})
        trends.append({
            "title":       lead.get('title', ''),
            "body":        summary or lead.get('body', '')[:400],
            "url":         lead.get('url', '#'),
            "source":      sources,
            "date":        lead.get('date', ''),
            "ai_compiled": False,
        })
    return trends[:3]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/news", methods=["POST"])
def get_news():
    topic = request.json.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "Please enter a topic."}), 400

    articles_raw   = []
    trend_articles = []

    try:
        with DDGS() as ddgs:
            # Fetch more results so we can filter / re-rank by source quality
            for item in ddgs.news(topic, max_results=15):
                articles_raw.append({
                    "title":  item.get("title", "No title"),
                    "body":   item.get("body", ""),
                    "url":    item.get("url", "#"),
                    "source": item.get("source", ""),
                    "date":   item.get("date", ""),
                })
            # Recent articles (past week) for trend synthesis
            for item in ddgs.news(topic, max_results=12, timelimit="w"):
                trend_articles.append({
                    "title":  item.get("title", "No title"),
                    "body":   item.get("body", ""),
                    "url":    item.get("url", "#"),
                    "source": item.get("source", ""),
                    "date":   item.get("date", ""),
                })
    except Exception as e:
        return jsonify({"error": f"Could not fetch news: {str(e)}"}), 500

    # Business-source-first ordering, capped at 5
    articles = _prioritize_news(articles_raw, limit=5)

    if not articles:
        return jsonify({"error": "No news found for this topic."}), 404

    # Try AI synthesis → fall back to keyword clustering
    trends = _compile_trends_claude(topic, trend_articles)
    if trends is None:
        trends = _compile_trends_simple(topic, trend_articles)

    return jsonify({"articles": articles, "trends": trends})


@app.route("/api/market", methods=["GET"])
def get_market():
    data = [_fetch_asset(asset) for asset in MARKET_ASSETS]
    return jsonify({"assets": data})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

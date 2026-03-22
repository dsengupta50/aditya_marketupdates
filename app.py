from flask import Flask, render_template, request, jsonify
import yfinance as yf
from duckduckgo_search import DDGS
import json

app = Flask(__name__)

MARKET_ASSETS = [
    {"name": "Dow Jones",         "ticker": "^DJI",  "label": "DOW"},
    {"name": "S&P 500",           "ticker": "^GSPC", "label": "S&P 500"},
    {"name": "NASDAQ",            "ticker": "^IXIC", "label": "NASDAQ"},
    {"name": "Gold",              "ticker": "GC=F",  "label": "Gold"},
    {"name": "10-Year Treasury",  "ticker": "^TNX",  "label": "10-Yr Treasury"},
]


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
                    "title": item.get("title", "No title"),
                    "body":  item.get("body", ""),
                    "url":   item.get("url", "#"),
                    "source": item.get("source", ""),
                    "date":  item.get("date", ""),
                })
    except Exception as e:
        return jsonify({"error": f"Could not fetch news: {str(e)}"}), 500

    if not results:
        return jsonify({"error": "No news found for this topic."}), 404

    return jsonify({"articles": results})


@app.route("/api/market", methods=["GET"])
def get_market():
    data = []
    for asset in MARKET_ASSETS:
        try:
            ticker = yf.Ticker(asset["ticker"])
            hist = ticker.history(period="1d", interval="5m")

            if hist.empty:
                # fallback: try 5-day data and take last day
                hist = ticker.history(period="5d", interval="15m")

            if hist.empty:
                data.append({
                    "name":   asset["name"],
                    "label":  asset["label"],
                    "ticker": asset["ticker"],
                    "error":  "No data available",
                })
                continue

            closes     = hist["Close"].dropna()
            timestamps = [str(t.strftime("%H:%M")) for t in closes.index]
            prices     = [round(float(p), 4) for p in closes.values]

            latest     = prices[-1] if prices else None
            open_price = float(hist["Open"].dropna().iloc[0]) if not hist["Open"].dropna().empty else None

            if latest is not None and open_price and open_price != 0:
                pct_change = round(((latest - open_price) / open_price) * 100, 2)
            else:
                pct_change = None

            data.append({
                "name":       asset["name"],
                "label":      asset["label"],
                "ticker":     asset["ticker"],
                "latest":     latest,
                "pct_change": pct_change,
                "timestamps": timestamps,
                "prices":     prices,
            })
        except Exception as e:
            data.append({
                "name":   asset["name"],
                "label":  asset["label"],
                "ticker": asset["ticker"],
                "error":  str(e),
            })

    return jsonify({"assets": data})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

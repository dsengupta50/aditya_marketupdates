"""Unit tests for app.py — all external calls are mocked."""
import json
import unittest
from unittest.mock import patch, MagicMock

import app as app_module
from app import (
    _is_business_source,
    _prioritize_news,
    _extract_keywords,
    _compile_trends_simple,
    _compile_trends_claude,
    _fetch_stooq,
    app as flask_app,
)


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_article(title="Headline", body="Body text here.", url="https://example.com/1",
                  source="Example", date="2026-03-24"):
    return {"title": title, "body": body, "url": url, "source": source, "date": date}


# ═══════════════════════════════════════════════════════════════════════════
# 1.  Business-source detection & prioritisation
# ═══════════════════════════════════════════════════════════════════════════

class TestBusinessSourceDetection(unittest.TestCase):

    def test_known_business_domains_recognised(self):
        cases = [
            "https://www.wsj.com/articles/fed-rates",
            "https://www.ft.com/content/abc",
            "https://bloomberg.com/news/fed",
            "https://reuters.com/business/fed",
            "https://cnbc.com/2026/03/24/rates.html",
            "https://marketwatch.com/story/rates",
            "https://www.nytimes.com/2026/business/fed.html",
            "https://barrons.com/articles/fed",
            "https://www.economist.com/finance",
            "https://fortune.com/2026/03/rates",
            "https://forbes.com/sites/fed-rates",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertTrue(_is_business_source(url), f"Expected business source: {url}")

    def test_non_business_domains_not_recognised(self):
        cases = [
            "https://techcrunch.com/article",
            "https://reddit.com/r/finance",
            "https://buzzfeed.com/story",
            "https://twitter.com/status/123",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertFalse(_is_business_source(url), f"Should NOT be business: {url}")

    def test_bad_url_returns_false(self):
        self.assertFalse(_is_business_source("not-a-url"))
        self.assertFalse(_is_business_source(""))


class TestPrioritizeNews(unittest.TestCase):

    def setUp(self):
        self.biz = _make_article(url="https://bloomberg.com/news/1", source="Bloomberg")
        self.ft  = _make_article(url="https://ft.com/content/2",      source="FT")
        self.gen = _make_article(url="https://techcrunch.com/3",       source="TechCrunch")
        self.gen2= _make_article(url="https://reddit.com/4",           source="Reddit")

    def test_business_sources_ranked_first(self):
        articles = [self.gen, self.biz, self.gen2, self.ft]
        result   = _prioritize_news(articles, limit=4)
        sources  = [a["source"] for a in result]
        self.assertEqual(sources[0], "Bloomberg")
        self.assertEqual(sources[1], "FT")

    def test_limit_respected(self):
        articles = [self.biz, self.ft, self.gen, self.gen2]
        result   = _prioritize_news(articles, limit=2)
        self.assertEqual(len(result), 2)

    def test_non_business_fills_remaining_slots(self):
        articles = [self.biz, self.gen, self.gen2]
        result   = _prioritize_news(articles, limit=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["source"], "Bloomberg")
        # Non-business fill the rest
        self.assertIn(result[1]["source"], {"TechCrunch", "Reddit"})

    def test_empty_list_returns_empty(self):
        self.assertEqual(_prioritize_news([], limit=5), [])


# ═══════════════════════════════════════════════════════════════════════════
# 2.  Keyword extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractKeywords(unittest.TestCase):

    def test_returns_significant_words(self):
        text = "The Federal Reserve raised interest rates sharply amid inflation concerns."
        kws  = _extract_keywords(text)
        self.assertIn("federal", kws)
        self.assertIn("raised", kws)

    def test_stopwords_excluded(self):
        text = "The the the and and and a a a"
        kws  = _extract_keywords(text)
        self.assertEqual(kws, [])  # all stopwords / too short

    def test_short_words_excluded(self):
        # Words shorter than 4 chars should be filtered
        text = "AI is big and hot now"
        kws  = _extract_keywords(text)
        for w in kws:
            self.assertGreaterEqual(len(w), 4)

    def test_top_n_respected(self):
        text = " ".join(["inflation"] * 5 + ["rates"] * 4 + ["economy"] * 3 +
                        ["market"] * 2 + ["stocks"] * 1 + ["bonds"] * 1)
        kws = _extract_keywords(text, top_n=3)
        self.assertLessEqual(len(kws), 3)


# ═══════════════════════════════════════════════════════════════════════════
# 3.  Simple trend compilation
# ═══════════════════════════════════════════════════════════════════════════

class TestCompileTrendsSimple(unittest.TestCase):

    def _make_cluster(self, keyword, n=3):
        """Return n articles all sharing `keyword`."""
        return [
            _make_article(
                title=f"{keyword.capitalize()} development {i+1}",
                body=f"The {keyword} market saw significant {keyword} activity. "
                     f"Analysts noted {keyword} implications for investors.",
                url=f"https://reuters.com/{keyword}/{i}",
                source="Reuters",
            )
            for i in range(n)
        ]

    def test_returns_three_trends(self):
        articles = (
            self._make_cluster("inflation") +
            self._make_cluster("interest") +
            self._make_cluster("mortgage")
        )
        trends = _compile_trends_simple("economy", articles)
        self.assertGreaterEqual(len(trends), 1)
        self.assertLessEqual(len(trends), 3)

    def test_each_trend_has_required_fields(self):
        articles = self._make_cluster("inflation", n=4)
        trends   = _compile_trends_simple("inflation", articles)
        for t in trends:
            for field in ("title", "body", "url", "source", "date", "ai_compiled"):
                self.assertIn(field, t)

    def test_ai_compiled_is_false(self):
        articles = self._make_cluster("rates", n=3)
        trends   = _compile_trends_simple("rates", articles)
        for t in trends:
            self.assertFalse(t["ai_compiled"])

    def test_empty_input_returns_empty(self):
        self.assertEqual(_compile_trends_simple("anything", []), [])

    def test_body_is_non_empty_string(self):
        articles = self._make_cluster("bonds", n=4)
        trends   = _compile_trends_simple("bonds", articles)
        for t in trends:
            self.assertIsInstance(t["body"], str)
            self.assertGreater(len(t["body"]), 0)


# ═══════════════════════════════════════════════════════════════════════════
# 4.  Claude trend compilation (mocked)
# ═══════════════════════════════════════════════════════════════════════════

MOCK_CLAUDE_RESPONSE = json.dumps([
    {
        "title":   "Central Banks Tighten Policy",
        "summary": "Multiple central banks raised rates this week, citing persistent inflation. "
                   "Analysts expect further hikes through mid-year.",
        "cited":   [1, 2],
    },
    {
        "title":   "Tech Sector Faces Valuation Reset",
        "summary": "High-growth tech stocks declined as rate expectations rose. "
                   "Earnings revisions are underway across the sector.",
        "cited":   [3],
    },
    {
        "title":   "Gold Surges as Safe Haven Demand Grows",
        "summary": "Gold hit a multi-month high amid uncertainty. "
                   "Investors shifted allocations toward commodities.",
        "cited":   [4, 5],
    },
])

class TestCompileTrendsClaude(unittest.TestCase):

    def _articles(self, n=6):
        return [
            _make_article(
                title=f"Article {i+1}",
                body=f"Content for article {i+1}.",
                url=f"https://reuters.com/{i}",
                source="Reuters",
                date="2026-03-24",
            )
            for i in range(n)
        ]

    def test_returns_none_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            # Ensure ANTHROPIC_API_KEY is absent
            import os; os.environ.pop("ANTHROPIC_API_KEY", None)
            result = _compile_trends_claude("rates", self._articles())
            self.assertIsNone(result)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    @patch("anthropic.Anthropic")
    def test_returns_three_trends_on_success(self, mock_anthropic_cls):
        mock_msg        = MagicMock()
        mock_msg.content[0].text = MOCK_CLAUDE_RESPONSE
        mock_client     = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        trends = _compile_trends_claude("markets", self._articles(6))
        self.assertIsNotNone(trends)
        self.assertEqual(len(trends), 3)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    @patch("anthropic.Anthropic")
    def test_each_trend_has_required_fields(self, mock_anthropic_cls):
        mock_msg        = MagicMock()
        mock_msg.content[0].text = MOCK_CLAUDE_RESPONSE
        mock_client     = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        trends = _compile_trends_claude("markets", self._articles(6))
        for t in trends:
            for field in ("title", "body", "url", "source", "date", "ai_compiled"):
                self.assertIn(field, t, f"Missing field '{field}' in {t}")

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    @patch("anthropic.Anthropic")
    def test_ai_compiled_is_true(self, mock_anthropic_cls):
        mock_msg        = MagicMock()
        mock_msg.content[0].text = MOCK_CLAUDE_RESPONSE
        mock_client     = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        trends = _compile_trends_claude("markets", self._articles(6))
        for t in trends:
            self.assertTrue(t["ai_compiled"])

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    @patch("anthropic.Anthropic")
    def test_strips_markdown_fences(self, mock_anthropic_cls):
        fenced = f"```json\n{MOCK_CLAUDE_RESPONSE}\n```"
        mock_msg        = MagicMock()
        mock_msg.content[0].text = fenced
        mock_client     = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        trends = _compile_trends_claude("markets", self._articles(6))
        self.assertIsNotNone(trends)
        self.assertEqual(len(trends), 3)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    @patch("anthropic.Anthropic")
    def test_returns_none_on_api_exception(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        mock_anthropic_cls.return_value = mock_client

        result = _compile_trends_claude("markets", self._articles(6))
        self.assertIsNone(result)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    @patch("anthropic.Anthropic")
    def test_returns_none_on_invalid_json(self, mock_anthropic_cls):
        mock_msg        = MagicMock()
        mock_msg.content[0].text = "not valid json"
        mock_client     = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        result = _compile_trends_claude("markets", self._articles(6))
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════
# 5.  /api/news Flask endpoint (DDGS mocked)
# ═══════════════════════════════════════════════════════════════════════════

MOCK_DDGS_ITEMS = [
    {"title": f"Article {i}", "body": f"Body {i}.",
     "url": url, "source": src, "date": "2026-03-24"}
    for i, (url, src) in enumerate([
        ("https://bloomberg.com/1",    "Bloomberg"),
        ("https://reuters.com/2",      "Reuters"),
        ("https://cnbc.com/3",         "CNBC"),
        ("https://techcrunch.com/4",   "TechCrunch"),
        ("https://reddit.com/r/5",     "Reddit"),
        ("https://wsj.com/6",          "WSJ"),
        ("https://ft.com/7",           "FT"),
        ("https://marketwatch.com/8",  "MarketWatch"),
    ])
]

class TestNewsEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()

    def _mock_ddgs_news(self, query, max_results=5, timelimit=None):
        return MOCK_DDGS_ITEMS[:max_results]

    @patch("app.DDGS")
    def test_returns_200_with_articles_and_trends(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.news.side_effect = self._mock_ddgs_news
        mock_ddgs_cls.return_value = mock_ddgs

        resp = self.client.post(
            "/api/news",
            json={"topic": "Federal Reserve"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("articles", data)
        self.assertIn("trends", data)

    @patch("app.DDGS")
    def test_business_sources_appear_first(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.news.side_effect = self._mock_ddgs_news
        mock_ddgs_cls.return_value = mock_ddgs

        resp = self.client.post("/api/news", json={"topic": "rates"})
        data = resp.get_json()

        business_names = {"Bloomberg", "Reuters", "CNBC", "WSJ", "FT", "MarketWatch"}
        for a in data["articles"]:
            # Every returned article should be from a business source
            # (we have more than 5 business sources in mock data)
            self.assertIn(a["source"], business_names,
                          f"Non-business source slipped in: {a['source']}")

    @patch("app.DDGS")
    def test_returns_max_5_articles(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.news.side_effect = self._mock_ddgs_news
        mock_ddgs_cls.return_value = mock_ddgs

        resp = self.client.post("/api/news", json={"topic": "markets"})
        data = resp.get_json()
        self.assertLessEqual(len(data["articles"]), 5)

    def test_missing_topic_returns_400(self):
        resp = self.client.post("/api/news", json={"topic": ""})
        self.assertEqual(resp.status_code, 400)

    def test_missing_body_returns_400(self):
        resp = self.client.post("/api/news", json={})
        self.assertEqual(resp.status_code, 400)

    @patch("app.DDGS")
    def test_ddgs_exception_returns_500(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.news.side_effect = Exception("network failure")
        mock_ddgs_cls.return_value = mock_ddgs

        resp = self.client.post("/api/news", json={"topic": "AI"})
        self.assertEqual(resp.status_code, 500)

    @patch("app.DDGS")
    def test_no_articles_returns_404(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.news.return_value = []
        mock_ddgs_cls.return_value = mock_ddgs

        resp = self.client.post("/api/news", json={"topic": "obscuretopicxyz"})
        self.assertEqual(resp.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════
# 6.  /api/market Flask endpoint
# ═══════════════════════════════════════════════════════════════════════════

class TestMarketEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()

    @patch("app._fetch_asset")
    def test_returns_200_with_assets(self, mock_fetch):
        mock_fetch.return_value = {
            "name": "S&P 500", "ticker": "^GSPC", "latest": 5200.0,
            "pct_change": 0.5, "timestamps": [], "prices": [],
            "source": "live", "fetched_at": "2026-03-24T12:00:00Z", "stale": False,
        }
        resp = self.client.get("/api/market")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("assets", data)
        self.assertEqual(len(data["assets"]), len(app_module.MARKET_ASSETS))

    @patch("app._fetch_asset")
    def test_all_five_assets_present(self, mock_fetch):
        mock_fetch.side_effect = lambda asset: {
            "name": asset["name"], "ticker": asset["ticker"],
            "latest": 100.0, "pct_change": 0.0, "timestamps": [], "prices": [],
            "source": "live", "fetched_at": "2026-03-24T12:00:00Z", "stale": False,
        }
        resp = self.client.get("/api/market")
        data = resp.get_json()
        tickers = {a["ticker"] for a in data["assets"]}
        expected = {"^DJI", "^GSPC", "^IXIC", "GC=F", "^TNX"}
        self.assertEqual(tickers, expected)


# ═══════════════════════════════════════════════════════════════════════════
# 7.  Stooq fallback parser
# ═══════════════════════════════════════════════════════════════════════════

class TestFetchStooq(unittest.TestCase):

    @patch("requests.get")
    def test_parses_valid_csv(self, mock_get):
        csv = "Symbol,Date,Time,Open,High,Low,Close,Volume\n^SPX,2026-03-24,16:00,5180.00,5210.00,5170.00,5200.00,0"
        mock_resp = MagicMock()
        mock_resp.text = csv
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        asset  = {"name": "S&P 500", "ticker": "^GSPC", "label": "S&P 500", "stooq": "^spx"}
        result = _fetch_stooq(asset)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["latest"], 5200.0)
        self.assertAlmostEqual(result["pct_change"],
                               round(((5200.0 - 5180.0) / 5180.0) * 100, 2))

    @patch("requests.get")
    def test_returns_none_on_nd_value(self, mock_get):
        csv = "Symbol,Date,Time,Open,High,Low,Close,Volume\n^SPX,2026-03-24,16:00,N/D,N/D,N/D,N/D,0"
        mock_resp = MagicMock()
        mock_resp.text = csv
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = _fetch_stooq({"name": "S&P 500", "ticker": "^GSPC",
                                "label": "S&P 500", "stooq": "^spx"})
        self.assertIsNone(result)

    @patch("requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        result = _fetch_stooq({"name": "S&P 500", "ticker": "^GSPC",
                                "label": "S&P 500", "stooq": "^spx"})
        self.assertIsNone(result)

    def test_returns_none_when_no_stooq_symbol(self):
        result = _fetch_stooq({"name": "S&P 500", "ticker": "^GSPC", "label": "S&P 500"})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

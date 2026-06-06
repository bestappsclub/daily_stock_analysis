# -*- coding: utf-8 -*-
"""Tests for the native US stock screening service."""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi import HTTPException

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.services import us_screener_service as uss
from src.services.us_screener_service import USScreenerService, MarketScreenerService


def _make_df(start_price: float, trend: str = "up", n: int = 70) -> pd.DataFrame:
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    if trend == "up":
        closes = [start_price * (1 + 0.01 * i) for i in range(n)]
    elif trend == "down":
        closes = [start_price * (1 - 0.006 * i) for i in range(n)]
    else:
        closes = [start_price for _ in range(n)]
    vol = [1_000_000 + i * 1000 for i in range(n)]
    pct = [0.0] + [(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(1, n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": vol,
            "amount": [c * v for c, v in zip(closes, vol)],
            "pct_chg": pct,
        }
    )


_DETERMINISTIC_ENV = {
    "US_SCREEN_ENABLED": "true",
    "US_SCREEN_LLM_RERANK": "false",
    "US_SCREEN_ENRICH": "false",
    "US_SCREEN_UNIVERSE": "AAA,BBB,CCC",
    "US_SCREEN_MAX_UNIVERSE": "1500",
    "US_SCREEN_USE_CACHE": "false",
}


class USScreenerStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = USScreenerService(config=SimpleNamespace())

    def test_status_reports_us_market(self) -> None:
        status = self.svc.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["supported_markets"], ["us"])
        self.assertEqual(status["strategy_count"], len(uss.US_STRATEGIES))

    def test_strategies_are_us_scoped(self) -> None:
        result = self.svc.strategies()
        ids = {s["id"] for s in result["strategies"]}
        self.assertIn("us_momentum", ids)
        for s in result["strategies"]:
            self.assertEqual(s["market_scope"], ["us"])


class USScreenerScreenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = USScreenerService(config=SimpleNamespace())

    @patch.dict("os.environ", _DETERMINISTIC_ENV, clear=False)
    def test_screen_returns_candidates_and_skips_missing(self) -> None:
        # CCC requested but has no data -> must be skipped (fail-open).
        frames = {"AAA": _make_df(100, "up"), "BBB": _make_df(40, "up")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames) as mocked:
            result = self.svc.screen(strategy="us_momentum", market="us", max_results=10)
        mocked.assert_called_once()
        self.assertTrue(result["enabled"])
        self.assertEqual(result["market"], "us")
        self.assertEqual(result["snapshot_count"], 2)
        self.assertEqual(result["snapshot_source"], "yfinance")
        codes = {c["code"] for c in result["candidates"]}
        self.assertTrue(codes.issubset({"AAA", "BBB"}))
        self.assertNotIn("CCC", codes)
        self.assertFalse(result["llm_ranked"])  # rerank disabled
        # candidate shape (normalized)
        first = result["candidates"][0]
        for key in ("rank", "code", "score", "reason", "factor_scores"):
            self.assertIn(key, first)
        self.assertIsInstance(first["factor_scores"], dict)

    @patch.dict("os.environ", _DETERMINISTIC_ENV, clear=False)
    def test_screen_respects_max_results(self) -> None:
        frames = {f"S{i}": _make_df(50 + i, "up") for i in range(8)}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = self.svc.screen(strategy="us_momentum", market="us", max_results=3)
        self.assertLessEqual(len(result["candidates"]), 3)

    @patch.dict("os.environ", _DETERMINISTIC_ENV, clear=False)
    def test_invalid_strategy_rejected(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.svc.screen(strategy="not_a_strategy", market="us", max_results=5)
        self.assertEqual(ctx.exception.status_code, 400)

    @patch.dict("os.environ", {**_DETERMINISTIC_ENV, "US_SCREEN_ENABLED": "false"}, clear=False)
    def test_disabled_rejected(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.svc.screen(strategy="us_momentum", market="us", max_results=5)
        self.assertEqual(ctx.exception.status_code, 403)

    @patch.dict("os.environ", _DETERMINISTIC_ENV, clear=False)
    def test_no_data_raises_424(self) -> None:
        with patch.object(uss, "batch_download_us_daily", return_value={}):
            with self.assertRaises(HTTPException) as ctx:
                self.svc.screen(strategy="us_momentum", market="us", max_results=5)
        self.assertEqual(ctx.exception.status_code, 424)


class SGScreenerTest(unittest.TestCase):
    def test_sg_status_and_strategies(self) -> None:
        svc = MarketScreenerService("sg", config=SimpleNamespace())
        self.assertEqual(svc.status()["supported_markets"], ["sg"])
        ids = {s["id"] for s in svc.strategies()["strategies"]}
        self.assertIn("sg_momentum", ids)
        for s in svc.strategies()["strategies"]:
            self.assertEqual(s["market_scope"], ["sg"])

    @patch.dict(
        "os.environ",
        {
            "SG_SCREEN_ENABLED": "true",
            "SG_SCREEN_LLM_RERANK": "false",
            "SG_SCREEN_ENRICH": "false",
            "SG_SCREEN_UNIVERSE": "D05.SI,O39.SI,U11.SI",
            "SG_SCREEN_USE_CACHE": "false",
        },
        clear=False,
    )
    def test_sg_screen(self) -> None:
        frames = {"D05.SI": _make_df(60, "up"), "O39.SI": _make_df(25, "up")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            svc = MarketScreenerService("sg", config=SimpleNamespace())
            result = svc.screen(strategy="sg_momentum", market="sg", max_results=5)
        self.assertEqual(result["market"], "sg")
        self.assertEqual(result["snapshot_count"], 2)
        codes = {c["code"] for c in result["candidates"]}
        self.assertTrue(codes.issubset({"D05.SI", "O39.SI"}))


class MarketRecognitionTest(unittest.TestCase):
    def test_sg_recognition_and_cn_us_hk_regression(self) -> None:
        from src.core.trading_calendar import get_market_for_stock
        from data_provider.base import normalize_stock_code, _market_tag

        # SG via .SI suffix
        self.assertEqual(get_market_for_stock("D05.SI"), "sg")
        self.assertEqual(normalize_stock_code("d05.si"), "D05.SI")
        self.assertEqual(_market_tag("9CI.SI"), "sg")
        # regression: existing markets unchanged
        self.assertEqual(get_market_for_stock("AAPL"), "us")
        self.assertEqual(get_market_for_stock("600519"), "cn")
        self.assertEqual(get_market_for_stock("HK00700"), "hk")


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeAdapter:
    """Stub LLMToolAdapter: is_available is a @property (matches real class)."""

    def __init__(self, config=None) -> None:  # noqa: D401
        pass

    @property
    def is_available(self) -> bool:
        return True

    def call_text(self, messages, **kwargs):  # noqa: ANN001
        return _FakeLLMResponse(
            '{"market_view":"看多","selection_logic":"动量优先",'
            '"portfolio_risk":"集中度风险","picks":[{"code":"AAA","llm_score":90,'
            '"llm_sector":"Tech","llm_thesis":"强趋势","llm_catalysts":["c1"],"llm_risks":["r1"]}]}'
        )


class LLMRerankPathTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_LLM_RERANK": "true"},
        clear=False,
    )
    def test_rerank_merges_llm_fields(self) -> None:
        frames = {"AAA": _make_df(100, "up"), "BBB": _make_df(40, "up")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames), patch(
            "src.agent.llm_adapter.LLMToolAdapter", _FakeAdapter
        ):
            svc = USScreenerService(config=SimpleNamespace())
            result = svc.screen(strategy="us_momentum", market="us", max_results=5)
        self.assertTrue(result["llm_ranked"])
        self.assertEqual(result["llm_market_view"], "看多")
        aaa = next(c for c in result["candidates"] if c["code"] == "AAA")
        self.assertEqual(aaa["llm_score"], 90)
        self.assertEqual(aaa["llm_thesis"], "强趋势")


def _zigzag_df(pivots, leg: int = 6) -> pd.DataFrame:
    """Build an OHLC df tracing the given swing pivots (for structure tests)."""
    closes: list = []
    for i in range(len(pivots) - 1):
        step = (pivots[i + 1] - pivots[i]) / leg
        closes += [pivots[i] + step * k for k in range(leg)]
    closes.append(pivots[-1])
    n = len(closes)
    return pd.DataFrame(
        {
            "date": [date(2025, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
            "amount": [c * 1_000_000 for c in closes],
            "pct_chg": [0.0] * n,
        }
    )


class SwingStructureTest(unittest.TestCase):
    def test_detect_bull_and_bear_structure(self) -> None:
        from src.stock_analyzer import StockTrendAnalyzer

        analyzer = StockTrendAnalyzer()
        bull = analyzer.analyze(_zigzag_df([100, 120, 110, 135, 125, 150, 140, 165]), "B")
        bear = analyzer.analyze(_zigzag_df([200, 180, 190, 165, 175, 150, 160, 135]), "S")
        self.assertEqual(bull.structure, "bull")
        self.assertEqual(bear.structure, "bear")


class StructureStrategyTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "BULL,BEARX"},
        clear=False,
    )
    def test_structure_bull_filters_to_bull_only(self) -> None:
        frames = {
            "BULL": _zigzag_df([100, 120, 110, 135, 125, 150, 140, 165]),
            "BEARX": _zigzag_df([200, 180, 190, 165, 175, 150, 160, 135]),
        }
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_structure_bull", market="us", max_results=5
            )
        codes = {c["code"] for c in result["candidates"]}
        self.assertIn("BULL", codes)
        self.assertNotIn("BEARX", codes)

    def test_structure_strategies_listed(self) -> None:
        ids = {s["id"] for s in MarketScreenerService("us", config=SimpleNamespace()).strategies()["strategies"]}
        self.assertIn("us_structure_bull", ids)
        self.assertIn("us_structure_bear", ids)


class DkIndicatorTest(unittest.TestCase):
    def test_dk_state_hold_on_uptrend_cash_on_downtrend(self) -> None:
        from src.stock_analyzer import StockTrendAnalyzer

        analyzer = StockTrendAnalyzer()
        up = analyzer.analyze(_make_df(100, "up"), "UP")
        down = analyzer.analyze(_make_df(100, "down"), "DOWN")
        self.assertEqual(up.dk_state, "hold")
        self.assertEqual(down.dk_state, "cash")


class DkStrategyTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "UP,DOWN"},
        clear=False,
    )
    def test_dk_buy_filters_to_hold_only(self) -> None:
        frames = {"UP": _make_df(100, "up"), "DOWN": _make_df(100, "down")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_dk_buy", market="us", max_results=5
            )
        codes = {c["code"] for c in result["candidates"]}
        self.assertIn("UP", codes)
        self.assertNotIn("DOWN", codes)

    def test_dk_strategy_listed_per_market(self) -> None:
        us_ids = {s["id"] for s in MarketScreenerService("us", config=SimpleNamespace()).strategies()["strategies"]}
        sg_ids = {s["id"] for s in MarketScreenerService("sg", config=SimpleNamespace()).strategies()["strategies"]}
        self.assertIn("us_dk_buy", us_ids)
        self.assertIn("sg_dk_buy", sg_ids)


class _FakeRepo:
    """Repo double: 'CACHED' has fresh cached bars, everything else is missing."""

    def get_range(self, code, start, end):
        if code == "CACHED":
            return [
                SimpleNamespace(
                    date=date.today() - timedelta(days=i),
                    open=10.0, high=11.0, low=9.0, close=10.0,
                    volume=1_000_000.0, amount=1e7, pct_chg=0.0,
                )
                for i in range(60)
            ]
        return []

    def save_dataframe(self, df, code, data_source="yfinance"):
        return len(df)


class PriceCacheTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "US_SCREEN_ENABLED": "true",
            "US_SCREEN_LLM_RERANK": "false",
            "US_SCREEN_ENRICH": "false",
            "US_SCREEN_UNIVERSE": "CACHED,MISS",
            "US_SCREEN_USE_CACHE": "true",
        },
        clear=False,
    )
    def test_cache_hit_only_fetches_missing(self) -> None:
        live = {"MISS": _make_df(50, "up")}
        with patch("src.repositories.stock_repo.StockRepository", return_value=_FakeRepo()), \
                patch.object(uss, "batch_download_us_daily", return_value=live) as mocked:
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_momentum", market="us", max_results=5
            )
        # live fetch must be called only for the stale/missing symbol
        mocked.assert_called_once()
        called_universe = list(mocked.call_args.args[0]) if mocked.call_args.args else list(mocked.call_args.kwargs.get("symbols", []))
        self.assertEqual(called_universe, ["MISS"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["snapshot_count"], 2)  # 1 cached + 1 live


if __name__ == "__main__":
    unittest.main()

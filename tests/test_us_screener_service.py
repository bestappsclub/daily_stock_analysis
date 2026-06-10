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
    "US_SCREEN_BENCHMARK": "",  # 关闭相对强弱基准抓取，保持这些用例的调用计数确定
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
            "SG_SCREEN_BENCHMARK": "",
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


def _dk_df(closes) -> pd.DataFrame:
    """Build an OHLCV df from a close series (high/low ±0.5%) for DK tests."""
    n = len(closes)
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
        "amount": [c * 1_000_000 for c in closes],
        "pct_chg": [0.0] * n,
    })


# 最新一根才突破 20 日高 → 当天出现 D 点
_D_TODAY = _dk_df([100.0] * 29 + [115.0])
# 先升入持股、最后一根跌破 10 日低 → 当天出现 K 点
_K_TODAY = _dk_df([100.0 + i for i in range(29)] + [80.0])
# 全程横盘 → 无 D/K
_FLAT = _dk_df([100.0] * 30)


class DkIndicatorTest(unittest.TestCase):
    def test_dk_state_hold_on_uptrend_cash_on_downtrend(self) -> None:
        from src.stock_analyzer import StockTrendAnalyzer

        analyzer = StockTrendAnalyzer()
        up = analyzer.analyze(_make_df(100, "up"), "UP")
        down = analyzer.analyze(_make_df(100, "down"), "DOWN")
        self.assertEqual(up.dk_state, "hold")
        self.assertEqual(down.dk_state, "cash")

    def test_dk_signal_and_days_since(self) -> None:
        from src.stock_analyzer import StockTrendAnalyzer

        analyzer = StockTrendAnalyzer()
        d = analyzer.analyze(_D_TODAY, "DT")
        k = analyzer.analyze(_K_TODAY, "KT")
        self.assertEqual(d.dk_signal, "D")
        self.assertEqual(d.dk_days_since, 0)
        self.assertEqual(d.dk_last_signal, "D")
        self.assertEqual(k.dk_signal, "K")
        self.assertEqual(k.dk_days_since, 0)
        # 摆动上涨但 D 点出现在更早的某根 → 当天无 signal、days_since>0
        up = analyzer.analyze(_make_df(100, "up"), "UP")
        self.assertEqual(up.dk_signal, "")
        self.assertGreater(up.dk_days_since, 0)
        self.assertEqual(up.dk_last_signal, "D")


class DkStrategyTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "DT,FLAT"},
        clear=False,
    )
    def test_dk_buy_keeps_only_today_d_points(self) -> None:
        frames = {"DT": _D_TODAY, "FLAT": _FLAT}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_dk_buy", market="us", max_results=5
            )
        codes = {c["code"] for c in result["candidates"]}
        self.assertEqual(codes, {"DT"})

    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "KT,FLAT"},
        clear=False,
    )
    def test_dk_sell_keeps_only_today_k_points(self) -> None:
        frames = {"KT": _K_TODAY, "FLAT": _FLAT}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_dk_sell", market="us", max_results=5
            )
        codes = {c["code"] for c in result["candidates"]}
        self.assertEqual(codes, {"KT"})

    def test_dk_strategy_listed_per_market(self) -> None:
        us_ids = {s["id"] for s in MarketScreenerService("us", config=SimpleNamespace()).strategies()["strategies"]}
        sg_ids = {s["id"] for s in MarketScreenerService("sg", config=SimpleNamespace()).strategies()["strategies"]}
        cn_ids = {s["id"] for s in MarketScreenerService("cn", config=SimpleNamespace()).strategies()["strategies"]}
        self.assertIn("us_dk_buy", us_ids)
        self.assertIn("us_dk_sell", us_ids)
        self.assertIn("sg_dk_buy", sg_ids)
        self.assertIn("cn_dk_buy", cn_ids)
        self.assertIn("cn_dk_sell", cn_ids)


class CnScreenerTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "CN_SCREEN_ENABLED": "true",
            "CN_SCREEN_LLM_RERANK": "false",
            "CN_SCREEN_ENRICH": "false",
            "CN_SCREEN_UNIVERSE": "600519,000001",
            "CN_SCREEN_USE_CACHE": "false",
            "CN_SCREEN_BENCHMARK": "",
        },
        clear=False,
    )
    def test_cn_screen_uses_akshare_batch(self) -> None:
        frames = {"600519": _make_df(1700, "up"), "000001": _make_df(11, "up")}
        with patch(
            "data_provider.akshare_fetcher.batch_download_cn_daily", return_value=frames
        ) as mocked:
            result = MarketScreenerService("cn", config=SimpleNamespace()).screen(
                strategy="cn_momentum", market="cn", max_results=5
            )
        mocked.assert_called_once()
        self.assertEqual(result["market"], "cn")
        self.assertEqual(result["snapshot_count"], 2)

    def test_cn_recognized_as_native_market(self) -> None:
        from src.services.us_screener_service import SUPPORTED_MARKETS
        self.assertIn("cn", SUPPORTED_MARKETS)


def _gap_df(last_open: float, base: float = 100.0, n: int = 30) -> pd.DataFrame:
    """Flat at `base`, with the last bar opening at `last_open` (creates a gap)."""
    opens = [base] * (n - 1) + [last_open]
    closes = [base] * (n - 1) + [last_open]
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


class GapTest(unittest.TestCase):
    def test_gap_detection(self) -> None:
        from src.stock_analyzer import StockTrendAnalyzer
        an = StockTrendAnalyzer()
        up = an.analyze(_gap_df(106.0), "GU")   # 末根开盘 +6%
        dn = an.analyze(_gap_df(94.0), "GD")     # 末根开盘 -6%
        flat = an.analyze(_gap_df(100.3), "GF")  # +0.3% < 阈值1%
        self.assertEqual(up.gap_dir, "up")
        self.assertEqual(up.gap_days_since, 0)
        self.assertEqual(dn.gap_dir, "down")
        self.assertEqual(flat.gap_dir, "")

    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "GU,GD,GF"},
        clear=False,
    )
    def test_gap_up_strategy_filters(self) -> None:
        frames = {"GU": _gap_df(106.0), "GD": _gap_df(94.0), "GF": _gap_df(100.3)}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_gap_up", market="us", max_results=5
            )
        self.assertEqual({c["code"] for c in result["candidates"]}, {"GU"})

    def test_gap_strategies_listed(self) -> None:
        ids = {s["id"] for s in MarketScreenerService("us", config=SimpleNamespace()).strategies()["strategies"]}
        self.assertIn("us_gap_up", ids)
        self.assertIn("us_gap_down", ids)


class ExitStopTest(unittest.TestCase):
    def test_chandelier_and_dk_trail(self) -> None:
        from src.stock_analyzer import StockTrendAnalyzer
        an = StockTrendAnalyzer()
        up = an.analyze(_make_df(100, "up"), "U")
        down = an.analyze(_make_df(100, "down"), "D")
        self.assertEqual(up.chandelier_dir, 1)
        self.assertGreater(up.chandelier_stop, 0)
        self.assertEqual(up.dk_state, "hold")
        self.assertGreater(up.dk_trail_stop, 0)
        self.assertIn("吊灯", up.exit_desc)
        self.assertEqual(down.chandelier_dir, -1)
        self.assertEqual(down.dk_trail_stop, 0.0)


def _flat_bench(n: int = 70, level: float = 1000.0) -> pd.DataFrame:
    """Flat benchmark index (date/close) aligned to _make_df dates for RS tests."""
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({"date": dates, "close": [level] * n})


class RsAdxStrategyTest(unittest.TestCase):
    def test_new_strategies_listed_per_market(self) -> None:
        for mkt in ("us", "sg", "hk", "cn"):
            ids = {s["id"] for s in MarketScreenerService(mkt, config=SimpleNamespace()).strategies()["strategies"]}
            self.assertIn(f"{mkt}_rs_leaders", ids)
            self.assertIn(f"{mkt}_trend_confirmed", ids)

    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "LEAD,LAGG"},
        clear=False,
    )
    def test_rs_leaders_filters_to_leading_bullish(self) -> None:
        frames = {"LEAD": _make_df(100, "up"), "LAGG": _make_df(100, "down")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames), \
                patch.object(MarketScreenerService, "_load_benchmark", return_value=_flat_bench()):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_rs_leaders", market="us", max_results=5
            )
        codes = {c["code"] for c in result["candidates"]}
        self.assertEqual(codes, {"LEAD"})
        lead = next(c for c in result["candidates"] if c["code"] == "LEAD")
        self.assertGreater(lead["factor_scores"]["rs_chg_pct"], 0)

    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "LEAD,LAGG"},
        clear=False,
    )
    def test_rs_leaders_failopen_without_benchmark(self) -> None:
        # 基准缺失（benchmark="" in env → _load_benchmark 返回 None）：RS 中性，
        # 仍能正常返回候选（降级为全集排序），不报错。
        frames = {"LEAD": _make_df(100, "up"), "LAGG": _make_df(100, "down")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_rs_leaders", market="us", max_results=5
            )
        self.assertTrue(result["enabled"])
        self.assertGreaterEqual(len(result["candidates"]), 1)

    @patch.dict(
        "os.environ",
        {**_DETERMINISTIC_ENV, "US_SCREEN_UNIVERSE": "TREND,FLAT"},
        clear=False,
    )
    def test_trend_confirmed_filters_trending_high_adx(self) -> None:
        frames = {"TREND": _make_df(100, "up"), "FLAT": _make_df(100, "flat")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames):
            result = USScreenerService(config=SimpleNamespace()).screen(
                strategy="us_trend_confirmed", market="us", max_results=5
            )
        codes = {c["code"] for c in result["candidates"]}
        self.assertIn("TREND", codes)
        self.assertNotIn("FLAT", codes)


class HkScreenerTest(unittest.TestCase):
    def test_hk_native_and_strategies(self) -> None:
        from src.services.us_screener_service import SUPPORTED_MARKETS
        self.assertIn("hk", SUPPORTED_MARKETS)
        ids = {s["id"] for s in MarketScreenerService("hk", config=SimpleNamespace()).strategies()["strategies"]}
        self.assertIn("hk_dk_buy", ids)
        self.assertIn("hk_momentum", ids)

    @patch.dict(
        "os.environ",
        {
            "HK_SCREEN_ENABLED": "true",
            "HK_SCREEN_LLM_RERANK": "false",
            "HK_SCREEN_ENRICH": "false",
            "HK_SCREEN_UNIVERSE": "0700.HK,0005.HK",
            "HK_SCREEN_USE_CACHE": "false",
            "HK_SCREEN_BENCHMARK": "",
        },
        clear=False,
    )
    def test_hk_screen_uses_yfinance_batch(self) -> None:
        frames = {"0700.HK": _make_df(400, "up"), "0005.HK": _make_df(60, "up")}
        with patch.object(uss, "batch_download_us_daily", return_value=frames) as mocked:
            result = MarketScreenerService("hk", config=SimpleNamespace()).screen(
                strategy="hk_momentum", market="hk", max_results=5
            )
        mocked.assert_called_once()
        self.assertEqual(result["market"], "hk")
        self.assertEqual(result["snapshot_count"], 2)


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
            "US_SCREEN_BENCHMARK": "",
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

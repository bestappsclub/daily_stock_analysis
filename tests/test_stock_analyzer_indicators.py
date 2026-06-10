# -*- coding: utf-8 -*-
"""Tests for ADX/DMI, OBV(量价确认/背离) 与相对强弱 RS 三个新增指标。"""

import unittest
from datetime import date, timedelta

import pandas as pd

from src.stock_analyzer import StockTrendAnalyzer


def _ohlcv(closes, volumes=None) -> pd.DataFrame:
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": volumes,
        "amount": [c * v for c, v in zip(closes, volumes)],
        "pct_chg": [0.0] * n,
    })


def _trend_df(start: float, slope: float, n: int = 70) -> pd.DataFrame:
    return _ohlcv([start * (1 + slope * i) for i in range(n)])


def _flat_close_df(level: float = 1000.0, n: int = 70) -> pd.DataFrame:
    """大盘基准用：仅需 date/close。"""
    return _ohlcv([level] * n)[["date", "close"]]


class AdxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.an = StockTrendAnalyzer()

    def test_strong_uptrend_has_high_adx_plus_di_dominant(self) -> None:
        res = self.an.analyze(_trend_df(100, 0.01), "UP")
        self.assertGreaterEqual(res.adx, StockTrendAnalyzer.ADX_TREND_MIN)
        self.assertGreater(res.plus_di, res.minus_di)
        self.assertIn(res.adx_status, ("trend", "strong_trend"))
        self.assertIn("ADX", res.adx_desc)

    def test_choppy_range_has_low_adx(self) -> None:
        closes = [100 + (1 if i % 2 == 0 else 0) for i in range(70)]  # 100/101 来回震荡
        res = self.an.analyze(_ohlcv(closes), "CHOP")
        self.assertLess(res.adx, StockTrendAnalyzer.ADX_TREND_MIN)
        self.assertEqual(res.adx_status, "range")


class ObvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.an = StockTrendAnalyzer()

    def test_price_up_volume_up_is_confirmation(self) -> None:
        res = self.an.analyze(_trend_df(100, 0.01), "CONF")  # 稳步上行、量平稳累加
        self.assertEqual(res.obv_divergence, "")
        self.assertEqual(res.obv_trend, "up")
        self.assertIn("量价配合", res.vol_confirm_desc)

    def test_bearish_divergence_price_up_obv_down(self) -> None:
        # 21 根：前 10 根下跌且放量，后 10 根上涨但缩量；
        # 末价高于窗口起点（价涨）但累计带符号量为负（OBV 跌）→ 顶背离。
        closes = [100.0]
        closes += [100.0 - 2.0 * i for i in range(1, 11)]   # 98 → 80
        closes += [80.0 + 2.1 * i for i in range(1, 11)]    # 82.1 → 101.0
        vols = [1000] + [1000] * 10 + [100] * 10
        res = self.an.analyze(_ohlcv(closes, vols), "DIV")
        self.assertEqual(res.obv_divergence, "bearish")
        self.assertTrue(any("OBV 顶背离" in r for r in res.risk_factors))


class RelativeStrengthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.an = StockTrendAnalyzer()

    def test_leading_when_outperforms_flat_benchmark(self) -> None:
        res = self.an.analyze(_trend_df(100, 0.01), "LEAD", benchmark_df=_flat_close_df())
        self.assertGreater(res.rs_chg_pct, 0)
        self.assertGreater(res.rs_ratio, 1.0)
        self.assertEqual(res.rs_status, "leading")
        self.assertIn("跑赢", res.rs_desc)

    def test_lagging_when_underperforms_flat_benchmark(self) -> None:
        res = self.an.analyze(_trend_df(100, -0.006), "LAG", benchmark_df=_flat_close_df())
        self.assertLess(res.rs_chg_pct, 0)
        self.assertEqual(res.rs_status, "lagging")

    def test_neutral_without_benchmark(self) -> None:
        res = self.an.analyze(_trend_df(100, 0.01), "N")  # benchmark_df=None
        self.assertEqual(res.rs_status, "neutral")
        self.assertEqual(res.rs_chg_pct, 0.0)
        self.assertEqual(res.rs_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()

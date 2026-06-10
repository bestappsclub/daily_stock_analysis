# -*- coding: utf-8 -*-
"""温斯坦四阶段 / 资金流 SMI / 新增选股策略分支（stage*/top_signal/smart_money）单元测试。"""

import unittest
from datetime import date, timedelta

import pandas as pd

from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult, TrendStatus, VolumeStatus
from src.services.us_screener_service import MarketScreenerService


def _df(closes, *, close_at_high: bool = False) -> pd.DataFrame:
    n = len(closes)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    if close_at_high:  # 收盘贴近最高 → 资金净流入（CMF≈1）
        high = list(closes)
        low = [c * 0.98 for c in closes]
    else:
        high = [c * 1.01 for c in closes]
        low = [c * 0.99 for c in closes]
    return pd.DataFrame({
        "date": dates,
        "open": list(closes),
        "high": high,
        "low": low,
        "close": list(closes),
        "volume": [1_000_000] * n,
        "amount": [c * 1_000_000 for c in closes],
        "pct_chg": [0.0] * n,
    })


class WeinsteinStageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.an = StockTrendAnalyzer()

    def test_uptrend_is_stage2(self) -> None:
        closes = [100 * (1 + 0.01 * i) for i in range(180)]
        self.assertEqual(self.an.analyze(_df(closes), "UP").weinstein_stage, 2)

    def test_downtrend_is_stage4(self) -> None:
        closes = [300 * (1 - 0.003 * i) for i in range(180)]
        self.assertEqual(self.an.analyze(_df(closes), "DOWN").weinstein_stage, 4)

    def test_short_history_stage_unknown(self) -> None:
        closes = [100 + i for i in range(60)]  # < 160 根，不足 30 周线
        self.assertEqual(self.an.analyze(_df(closes), "SHORT").weinstein_stage, 0)


class SmiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.an = StockTrendAnalyzer()

    def test_close_at_high_means_positive_inflow(self) -> None:
        closes = [100 + i * 0.1 for i in range(40)]
        self.assertGreater(self.an.analyze(_df(closes, close_at_high=True), "IN").smi, 0.5)


class ApplyStrategyTest(unittest.TestCase):
    @staticmethod
    def _tr(code: str, **kw) -> TrendAnalysisResult:
        tr = TrendAnalysisResult(code=code)
        for key, value in kw.items():
            setattr(tr, key, value)
        return tr

    def test_stage2_filters_and_ranks_by_rs(self) -> None:
        scored = [
            self._tr("A", weinstein_stage=2, rs_chg_pct=5.0),
            self._tr("B", weinstein_stage=1, rs_chg_pct=99.0),
            self._tr("C", weinstein_stage=2, rs_chg_pct=9.0),
        ]
        out = MarketScreenerService._apply_strategy("stage2_strong_up", scored)
        self.assertEqual([t.code for t in out], ["C", "A"])  # 仅 stage2，强者(RS高)在前

    def test_top_signal_includes_stage34_dkK_bear(self) -> None:
        scored = [
            self._tr("ok", weinstein_stage=2),
            self._tr("t3", weinstein_stage=3),
            self._tr("k", dk_signal="K"),
            self._tr("bear", structure="bear"),
        ]
        out = MarketScreenerService._apply_strategy("top_signal", scored)
        self.assertEqual({t.code for t in out}, {"t3", "k", "bear"})

    def test_smart_money_filters_positive_smi(self) -> None:
        scored = [self._tr("pos", smi=0.3), self._tr("neg", smi=-0.2), self._tr("hi", smi=0.6)]
        out = MarketScreenerService._apply_strategy("smart_money", scored)
        self.assertEqual([t.code for t in out], ["hi", "pos"])  # 仅 smi>0，强者在前

    def test_power_setup_requires_triple_confluence(self) -> None:
        # 三重共振：结构 bull + 多头趋势(均线多排) + 放量上涨，三者缺一不可
        good = self._tr("GOOD", structure="bull", trend_status=TrendStatus.BULL,
                        volume_status=VolumeStatus.HEAVY_VOLUME_UP, weinstein_stage=2, rs_status="leading", pwr=90)
        weak = self._tr("WEAK", structure="bull", trend_status=TrendStatus.BULL,
                        volume_status=VolumeStatus.HEAVY_VOLUME_UP, weinstein_stage=0, pwr=10)
        no_struct = self._tr("NOSTRUCT", structure="", trend_status=TrendStatus.BULL,
                             volume_status=VolumeStatus.HEAVY_VOLUME_UP)
        no_vol = self._tr("NOVOL", structure="bull", trend_status=TrendStatus.BULL,
                          volume_status=VolumeStatus.NORMAL)
        out = MarketScreenerService._apply_strategy("power_setup", [no_struct, weak, no_vol, good])
        # 仅三重共振者入选；教科书(阶段2+领涨+高PWR)在前
        self.assertEqual([t.code for t in out], ["GOOD", "WEAK"])


if __name__ == "__main__":
    unittest.main()

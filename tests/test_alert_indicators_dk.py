# -*- coding: utf-8 -*-
"""Tests for the dk_signal technical alert type (DK 买卖点告警)."""

import unittest
from datetime import date, timedelta

import pandas as pd

from src.services.alert_indicators import (
    TECHNICAL_ALERT_TYPES,
    compute_required_bars,
    evaluate_indicator_alert,
    normalize_indicator_parameters,
)


def _ohlcv(closes, volumes) -> pd.DataFrame:
    n = len(closes)
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": volumes,
    })


# 前 69 根横盘、最后一根放量突破 → 当日 D 点（买点）
_D_TODAY = _ohlcv([100.0] * 69 + [118.0], [1_000_000] * 69 + [5_000_000])
# 先升入持股，最后一根跌破破位 → 当日 K 点（卖点）
_K_TODAY = _ohlcv([100.0 + i for i in range(69)] + [80.0], [1_000_000] * 70)


class DkSignalAlertTest(unittest.TestCase):
    def test_registered_as_technical_type(self) -> None:
        self.assertIn("dk_signal", TECHNICAL_ALERT_TYPES)

    def test_normalize_direction(self) -> None:
        self.assertEqual(normalize_indicator_parameters("dk_signal", {}), {"direction": "both"})
        self.assertEqual(normalize_indicator_parameters("dk_signal", {"direction": "buy"}), {"direction": "buy"})
        self.assertEqual(normalize_indicator_parameters("dk_signal", {"direction": "sell"}), {"direction": "sell"})
        with self.assertRaises(ValueError):
            normalize_indicator_parameters("dk_signal", {"direction": "nonsense"})

    def test_required_bars(self) -> None:
        self.assertEqual(compute_required_bars("dk_signal", {"direction": "both"}), 60)

    def test_buy_direction_triggers_on_d_point(self) -> None:
        ev = evaluate_indicator_alert("dk_signal", "T", {"direction": "buy"}, _D_TODAY)
        self.assertEqual(ev.status, "triggered")
        ev_sell = evaluate_indicator_alert("dk_signal", "T", {"direction": "sell"}, _D_TODAY)
        self.assertEqual(ev_sell.status, "not_triggered")

    def test_sell_direction_triggers_on_k_point(self) -> None:
        ev = evaluate_indicator_alert("dk_signal", "T", {"direction": "sell"}, _K_TODAY)
        self.assertEqual(ev.status, "triggered")
        ev_buy = evaluate_indicator_alert("dk_signal", "T", {"direction": "buy"}, _K_TODAY)
        self.assertEqual(ev_buy.status, "not_triggered")

    def test_both_triggers_on_either(self) -> None:
        self.assertEqual(evaluate_indicator_alert("dk_signal", "T", {"direction": "both"}, _D_TODAY).status, "triggered")
        self.assertEqual(evaluate_indicator_alert("dk_signal", "T", {"direction": "both"}, _K_TODAY).status, "triggered")

    def test_insufficient_data_degrades(self) -> None:
        short = _ohlcv([100.0] * 30, [1_000_000] * 30)
        ev = evaluate_indicator_alert("dk_signal", "T", {"direction": "both"}, short)
        self.assertEqual(ev.status, "degraded")


if __name__ == "__main__":
    unittest.main()

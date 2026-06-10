# -*- coding: utf-8 -*-
"""选股 + 买卖点日报：跑一个策略选出标的，并给每只票一个明确的"买/持/卖/观望"判定 + 止损价。

离线、秒级：直接读本地 ``data/stock_analysis.db`` 缓存（先 ``scripts/sync_prices.py`` 灌库）。
复用真实选股排序 ``MarketScreenerService._apply_strategy`` 与趋势引擎 ``StockTrendAnalyzer``，
保证与 Web「选股」页同一套逻辑。相对强弱 RS 用样本股票池等权指数作基准（离线代理）。

判定规则（买卖点）：
  🔴 卖/避：当日 K 点 / 吊灯翻空 / 跌破吊灯止损 / OBV 顶背离
  🟢 买  ：当日 D 点(买点触发) 或 (相对强弱领涨 + 多头 + 吊灯多 + 持股态)
  🟡 持有：持股态 + 多头(未达强买)
  ⚪ 观望：其余

用法：
  python scripts/daily_signals.py                          # 默认 us / rs_leaders / Top15
  python scripts/daily_signals.py --strategy dk_buy        # 今日刚出 D 点(买点)
  python scripts/daily_signals.py --strategy trend_confirmed --top 20
  python scripts/daily_signals.py --watchlist AAPL,NVDA,MSFT   # 只看自选
策略可选：rs_leaders / dk_buy / dk_sell / trend_confirmed / breakout / momentum /
          structure_bull / oversold / gap_up / gap_down
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.stock_analyzer import StockTrendAnalyzer, TrendStatus, TrendAnalysisResult  # noqa: E402
from src.services.us_screener_service import MarketScreenerService  # noqa: E402
from backtest_rule import DB_PATH, load_universe, load_frames, build_equal_weight_benchmark  # noqa: E402

BULLISH = {TrendStatus.STRONG_BULL, TrendStatus.BULL, TrendStatus.WEAK_BULL}


def verdict(r: TrendAnalysisResult) -> str:
    """把指标翻译成一句买卖判定。"""
    if (r.dk_signal == "K" or r.chandelier_dir == -1
            or (r.chandelier_stop and r.current_price < r.chandelier_stop)
            or r.obv_divergence == "bearish"):
        return "🔴 卖/避"
    if r.dk_signal == "D":
        return "🟢 买(今日D点)"
    if r.dk_state == "hold" and r.trend_status in BULLISH:
        if r.rs_status == "leading" and r.chandelier_dir == 1:
            return "🟢 买/持(强势)"
        return "🟡 持有"
    return "⚪ 观望"


def rs_tag(r: TrendAnalysisResult) -> str:
    if r.rs_status == "leading":
        return f"领涨{r.rs_chg_pct:+.0f}%"
    if r.rs_status == "lagging":
        return f"落后{r.rs_chg_pct:+.0f}%"
    return "—"


def dk_tag(r: TrendAnalysisResult) -> str:
    if r.dk_signal == "D":
        return "D点·今日"
    if r.dk_signal == "K":
        return "K点·今日"
    if r.dk_last_signal and r.dk_days_since >= 0:
        return f"{r.dk_last_signal}点·{r.dk_days_since}天前"
    return r.dk_state or "—"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="rs_leaders")
    ap.add_argument("--market", default="us", help="目前脚本仅内置 us 缓存加载")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--sample", type=int, default=400, help="扫描股票数（0=全部缓存美股）")
    ap.add_argument("--min-bars", type=int, default=80)
    ap.add_argument("--watchlist", default="", help="逗号分隔代码，只看这些（忽略 sample）")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"找不到缓存 DB：{DB_PATH}，请先 python scripts/sync_prices.py")
        sys.exit(1)

    con = sqlite3.connect(str(DB_PATH))
    if args.watchlist.strip():
        codes = [c.strip().upper() for c in args.watchlist.split(",") if c.strip()]
    else:
        codes = load_universe(con, args.sample, args.min_bars)
    frames = load_frames(con, codes)
    con.close()
    if not frames:
        print("无可用行情（缓存为空？先 python scripts/sync_prices.py）")
        sys.exit(1)
    bench_df = build_equal_weight_benchmark(frames)

    analyzer = StockTrendAnalyzer()
    scored: List[TrendAnalysisResult] = []
    for code, df in frames.items():
        try:
            scored.append(analyzer.analyze(df, code, benchmark_df=bench_df))
        except Exception:  # noqa: BLE001 - 单只失败跳过
            continue

    ranked = MarketScreenerService._apply_strategy(args.strategy, scored)
    top = ranked[: max(args.top, 1)]

    title = {
        "rs_leaders": "相对强弱领涨", "dk_buy": "今日DK买点", "dk_sell": "今日DK卖点",
        "trend_confirmed": "ADX趋势确认", "breakout": "放量突破", "momentum": "趋势动量",
        "structure_bull": "多头结构", "oversold": "超跌反转",
        "gap_up": "向上跳空", "gap_down": "向下跳空",
    }.get(args.strategy, args.strategy)

    print(f"\n选股策略：{title}（{args.strategy}）  扫描 {len(scored)} 只  "
          f"基准日期 {bench_df['date'].iloc[-1]}")
    print("=" * 92)
    print(f"{'#':>2} {'代码':<8} {'现价':>9} {'判定':<14} {'趋势':<10} "
          f"{'相对强弱':<12} {'DK':<13} {'止损价':>9}")
    print("-" * 92)
    for i, r in enumerate(top, 1):
        price = f"{r.current_price:.2f}" if r.current_price else "—"
        stop = f"{r.chandelier_stop:.2f}" if r.chandelier_stop else "—"
        trend = r.trend_status.value
        print(f"{i:>2} {r.code:<8} {price:>9} {verdict(r):<13} {trend:<10} "
              f"{rs_tag(r):<12} {dk_tag(r):<13} {stop:>9}")
    print("=" * 92)
    print("买卖点：🟢可买  🟡持有  🔴卖/避  ⚪观望 ｜ 止损=吊灯止损价（跌破即离场）")
    print("提示：买入当天就把止损价记下并执行；这是工具输出，非投资建议，仓位务必控制。")


if __name__ == "__main__":
    main()

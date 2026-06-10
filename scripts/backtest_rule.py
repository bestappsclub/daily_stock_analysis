# -*- coding: utf-8 -*-
"""轻量级策略回测（walk-forward），用于验证选股/买卖规则是否有 edge。

与 ``src/core/backtest_engine.py`` 区别：那个引擎评估**历史 LLM 分析建议**是否事后正确；
本脚本做的是**策略级 P&L 回测**——逐日用真实的 ``StockTrendAnalyzer`` 生成信号、模拟开平仓、
统计胜率/盈亏比/最大单笔亏损，并与买入持有对比。

当前内置规则（可扩展）：
  - 入场：相对强弱领涨（rs_status==leading 且多头趋势）且吊灯方向为多（chandelier_dir==1）
  - 出场：吊灯止损翻空（chandelier_dir==-1）
执行价：信号日 t 的**次日开盘**（避免用当日收盘下单的前视偏差）。

数据来源：本地 ``data/stock_analysis.db`` 的 ``stock_daily`` 缓存（离线，不联网）。
基准（相对强弱用）：样本股票池的**等权指数**（各股归一到起点后按日取均值）。

用法：
  python scripts/backtest_rule.py                 # 默认采样 250 只美股
  python scripts/backtest_rule.py --sample 400    # 采样更多
  python scripts/backtest_rule.py --rule rs_chandelier
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.stock_analyzer import StockTrendAnalyzer, TrendStatus  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "stock_analysis.db"
BULLISH = {TrendStatus.STRONG_BULL, TrendStatus.BULL, TrendStatus.WEAK_BULL}


def _is_us(code: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", code)) and not code.endswith((".SI", ".HK"))


def load_universe(con: sqlite3.Connection, sample: int, min_bars: int) -> List[str]:
    rows = con.execute("SELECT code, COUNT(*) c FROM stock_daily GROUP BY code").fetchall()
    us = sorted([(c, n) for c, n in rows if _is_us(c) and n >= min_bars], key=lambda x: -x[1])
    codes = [c for c, _ in us]
    # 取覆盖最好的一批，再均匀采样，避免只测同一段时间最长的几只
    if sample and len(codes) > sample:
        step = len(codes) / sample
        codes = [codes[int(i * step)] for i in range(sample)]
    return codes


def load_frames(con: sqlite3.Connection, codes: List[str]) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}
    for code in codes:
        cur = con.execute(
            "SELECT date, open, high, low, close, volume FROM stock_daily "
            "WHERE code=? ORDER BY date", (code,),
        )
        recs = cur.fetchall()
        if len(recs) < 80:
            continue
        df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"]).dt.date
        frames[code] = df
    return frames


def build_equal_weight_benchmark(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """等权指数：每只股票归一到自身首个收盘价，再按日期对所有股票取均值。"""
    norm = []
    for df in frames.values():
        s = df.set_index("date")["close"]
        if s.iloc[0] > 0:
            norm.append(s / s.iloc[0])
    mat = pd.concat(norm, axis=1)
    bench = mat.mean(axis=1, skipna=True)
    return pd.DataFrame({"date": bench.index, "close": bench.values}).sort_values("date").reset_index(drop=True)


def backtest(frames: Dict[str, pd.DataFrame], bench_df: pd.DataFrame,
             warmup: int = 65, verbose_every: int = 50) -> Dict:
    analyzer = StockTrendAnalyzer()
    trades: List[Dict] = []
    bh_returns: List[float] = []  # 同窗口买入持有对照（每只股票）
    n_codes = len(frames)
    t0 = time.time()

    for idx, (code, df) in enumerate(frames.items(), 1):
        n = len(df)
        if n < warmup + 5:
            continue
        opens = df["open"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)

        # 买入持有对照：warmup 次日开盘买 → 最后一根收盘
        if opens[warmup] > 0:
            bh_returns.append(closes[-1] / opens[warmup] - 1.0)

        in_pos = False
        entry_price = 0.0
        entry_i = -1
        # 逐日：用 df[:t+1] 出信号，次日 t+1 开盘执行
        for t in range(warmup, n - 1):
            sub = df.iloc[: t + 1]
            res = analyzer.analyze(sub, code, benchmark_df=bench_df)
            exec_open = opens[t + 1]
            if exec_open <= 0:
                continue
            if not in_pos:
                enter = (res.rs_status == "leading" and res.trend_status in BULLISH
                         and res.chandelier_dir == 1)
                if enter:
                    in_pos = True
                    entry_price = exec_open
                    entry_i = t + 1
            else:
                if res.chandelier_dir == -1:
                    trades.append({
                        "code": code, "ret": exec_open / entry_price - 1.0,
                        "hold": (t + 1) - entry_i, "exit": "chandelier",
                    })
                    in_pos = False
        # 收尾：仍持仓 → 末根收盘平（标记，不算"规则触发"出场质量）
        if in_pos and entry_price > 0:
            trades.append({
                "code": code, "ret": closes[-1] / entry_price - 1.0,
                "hold": (n - 1) - entry_i, "exit": "eod",
            })
        if verbose_every and idx % verbose_every == 0:
            print(f"  ...{idx}/{n_codes} 只已回测，累计 {len(trades)} 笔交易，"
                  f"用时 {time.time() - t0:.0f}s", flush=True)

    return summarize(trades, bh_returns)


def summarize(trades: List[Dict], bh_returns: List[float]) -> Dict:
    rets = np.array([t["ret"] for t in trades], dtype=float)
    out: Dict = {"n_trades": len(trades)}
    if len(rets) == 0:
        return out
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    out.update({
        "win_rate": len(wins) / len(rets),
        "avg_ret": rets.mean(),
        "median_ret": float(np.median(rets)),
        "avg_win": wins.mean() if len(wins) else 0.0,
        "avg_loss": losses.mean() if len(losses) else 0.0,
        "worst": rets.min(),
        "best": rets.max(),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_hold": float(np.mean([t["hold"] for t in trades])),
        "exits": {k: sum(1 for t in trades if t["exit"] == k) for k in ("chandelier", "eod")},
        "expectancy": rets.mean(),  # 每笔期望收益
    })
    if bh_returns:
        bh = np.array(bh_returns, dtype=float)
        out["buyhold_avg"] = bh.mean()
        out["buyhold_worst"] = bh.min()
        out["buyhold_median"] = float(np.median(bh))
    return out


def fmt_pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=250, help="采样股票数（0=全部）")
    ap.add_argument("--min-bars", type=int, default=140)
    ap.add_argument("--warmup", type=int, default=65)
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"找不到缓存 DB：{DB_PATH}，请先 python scripts/sync_prices.py")
        sys.exit(1)

    con = sqlite3.connect(str(DB_PATH))
    codes = load_universe(con, args.sample, args.min_bars)
    print(f"股票池：{len(codes)} 只美股（min_bars={args.min_bars}）")
    frames = load_frames(con, codes)
    con.close()
    print(f"成功加载行情：{len(frames)} 只")
    bench_df = build_equal_weight_benchmark(frames)
    print(f"等权基准指数：{len(bench_df)} 个交易日 "
          f"({bench_df['date'].iloc[0]} → {bench_df['date'].iloc[-1]})")
    print("规则：入场=相对强弱领涨+多头+吊灯多 / 出场=吊灯翻空（次日开盘执行）")
    print("开始 walk-forward 回测...\n")

    r = backtest(frames, bench_df, warmup=args.warmup)

    print("\n" + "=" * 56)
    print("  回测结果：rs_leaders 入场 + 吊灯止损出场")
    print("=" * 56)
    if r["n_trades"] == 0:
        print("  无交易（窗口太短或无信号）。")
        return
    print(f"  交易笔数        {r['n_trades']}")
    print(f"  胜率            {r['win_rate'] * 100:.1f}%")
    print(f"  每笔期望收益    {fmt_pct(r['expectancy'])}   <- 关键：>0 才有 edge")
    print(f"  平均收益/笔     {fmt_pct(r['avg_ret'])}")
    print(f"  收益中位数      {fmt_pct(r['median_ret'])}")
    print(f"  平均盈利        {fmt_pct(r['avg_win'])}")
    print(f"  平均亏损        {fmt_pct(r['avg_loss'])}   <- 止损是否管住亏损")
    print(f"  最差单笔        {fmt_pct(r['worst'])}")
    print(f"  最好单笔        {fmt_pct(r['best'])}")
    pf = r["profit_factor"]
    print(f"  盈亏比(PF)      {'∞' if pf == float('inf') else f'{pf:.2f}'}   <- >1.3 较稳健")
    print(f"  平均持仓        {r['avg_hold']:.0f} 个交易日")
    print(f"  出场构成        吊灯止损 {r['exits']['chandelier']} / 持有到末 {r['exits']['eod']}")
    if "buyhold_avg" in r:
        print("-" * 56)
        print(f"  对照·买入持有   平均 {fmt_pct(r['buyhold_avg'])} / "
              f"中位 {fmt_pct(r['buyhold_median'])} / 最差 {fmt_pct(r['buyhold_worst'])}")
        edge = r["expectancy"] - r["buyhold_avg"]
        print(f"  规则 vs 持有    每笔期望 {fmt_pct(r['expectancy'])} vs 持有 {fmt_pct(r['buyhold_avg'])}"
              f"（差 {fmt_pct(edge)}）")
    print("=" * 56)
    print("注：窗口约 150 个交易日，样本短，仅作方向性判断，非多年验证。")


if __name__ == "__main__":
    main()

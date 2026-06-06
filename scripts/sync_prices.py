# -*- coding: utf-8 -*-
"""把美股 / 新加坡股票池的日线行情同步进本地数据库 `stock_daily`（选股缓存）。

选股器（``MarketScreenerService``）默认优先读本地 ``stock_daily`` 缓存，只对缺失/
过期标的 live 补抓。本脚本批量灌库 / 增量刷新该缓存，让全市场扫描秒级、可离线、不限流。

数据库为本地 SQLite（``data/stock_analysis.db``，已 gitignore，不入库）。

用法：

    python scripts/sync_prices.py                      # 同步 us+sg，默认 150 天，增量
    python scripts/sync_prices.py --markets us         # 只同步美股
    python scripts/sync_prices.py --days 500           # 抓约 2 年
    python scripts/sync_prices.py --full               # 忽略新鲜度，全部重抓
    python scripts/sync_prices.py --max 50             # 每市场最多 50 只（测试用）

增量逻辑：某标的本地最新日期在 ``--stale-days``（默认 2）天内则跳过，否则重抓并 upsert。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_provider.yfinance_fetcher import batch_download_us_daily  # noqa: E402
from src.repositories.stock_repo import StockRepository  # noqa: E402
from src.services.us_screener_service import MarketScreenerService, SUPPORTED_MARKETS  # noqa: E402


def _needs_refresh(repo: StockRepository, code: str, start: date, today: date,
                   fresh_cutoff: date, full: bool) -> bool:
    if full:
        return True
    try:
        rows = repo.get_range(code, start, today)
    except Exception:  # noqa: BLE001
        return True
    return not (rows and len(rows) >= 20 and max(r.date for r in rows) >= fresh_cutoff)


def sync_market(market: str, *, days: int, stale_days: int, full: bool,
                cap: int, chunk: int = 150) -> tuple[int, int, int]:
    """同步单个市场，返回 (universe, refreshed, saved_rows)。"""
    svc = MarketScreenerService(market)
    universe = svc._load_universe()
    if cap > 0:
        universe = universe[:cap]
    if not universe:
        print(f"[{market}] 股票池为空，跳过")
        return 0, 0, 0

    repo = StockRepository()
    today = date.today()
    start = today - timedelta(days=days)
    fresh_cutoff = today - timedelta(days=max(stale_days, 0))

    stale = [c for c in universe if _needs_refresh(repo, c, start, today, fresh_cutoff, full)]
    print(f"[{market}] 股票池 {len(universe)} 只；需刷新 {len(stale)} 只（其余缓存已新鲜）")

    saved_rows = 0
    refreshed = 0
    for i in range(0, len(stale), chunk):
        batch = stale[i:i + chunk]
        frames = batch_download_us_daily(batch, days=days)
        for code, df in frames.items():
            try:
                saved_rows += repo.save_dataframe(df, code, data_source="yfinance")
                refreshed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 保存失败 {code}: {exc}")
        print(f"  [{market}] 进度 {min(i + chunk, len(stale))}/{len(stale)}")
        time.sleep(0.5)  # 轻微限速，礼貌对待数据源

    print(f"[{market}] 完成：刷新 {refreshed}/{len(stale)}，新增约 {saved_rows} 行")
    return len(universe), refreshed, saved_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="同步美股/新加坡日线到本地 stock_daily 缓存")
    parser.add_argument("--markets", default="us,sg", help="逗号分隔：us,sg（默认两者）")
    parser.add_argument("--days", type=int, default=150, help="回看自然日（默认 150）")
    parser.add_argument("--stale-days", type=int, default=2, help="缓存新鲜度阈值（默认 2 天）")
    parser.add_argument("--full", action="store_true", help="忽略新鲜度，全部重抓")
    parser.add_argument("--max", type=int, default=0, help="每市场最多同步多少只（0=不限，测试用）")
    args = parser.parse_args()

    markets = [m.strip().lower() for m in args.markets.split(",") if m.strip()]
    bad = [m for m in markets if m not in SUPPORTED_MARKETS]
    if bad:
        print(f"[ERROR] 不支持的市场：{bad}（可选 {list(SUPPORTED_MARKETS)}）", file=sys.stderr)
        return 1

    t0 = time.time()
    totals = {"universe": 0, "refreshed": 0, "rows": 0}
    for m in markets:
        u, r, rows = sync_market(
            m, days=args.days, stale_days=args.stale_days, full=args.full, cap=args.max
        )
        totals["universe"] += u
        totals["refreshed"] += r
        totals["rows"] += rows

    dt = time.time() - t0
    print(
        f"\n=== 同步完成（{dt:.1f}s）：股票池 {totals['universe']} 只，"
        f"刷新 {totals['refreshed']} 只，新增约 {totals['rows']} 行 ==="
    )
    print("提示：数据库 data/stock_analysis.db 已 gitignore，不要提交；选股器会自动读缓存。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

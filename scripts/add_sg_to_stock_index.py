# -*- coding: utf-8 -*-
"""把新加坡(SGX)主板股票追加进前端自动补全索引 ``apps/dsa-web/public/stocks.index.json``。

前端「个股分析」输入框的自动补全读取该索引（压缩 tuple 格式）。索引由
``scripts/generate_index_from_csv.py`` 从 Tushare/AkShare 的 A股/港股/美股 CSV 构建，
**不含新加坡**（数据源不覆盖 SGX）。本脚本从 SGX 官方证券列表补齐 SG 条目：

- canonicalCode = ``<代码>.SI``（如 ``D05.SI``）—— 选中候选时即以此提交，后端据
  ``.SI`` 后缀识别为 sg 市场。
- displayCode = 裸代码（``D05``），market = ``SG``。

幂等：按 canonicalCode 去重，已存在则跳过，可反复运行。SG 之外的条目原样保留。

用法：

    python scripts/add_sg_to_stock_index.py
    python scripts/add_sg_to_stock_index.py --dry-run   # 只打印将追加多少，不写文件

注意：写入后需重新构建前端（``cd apps/dsa-web && npm run build``）索引才会随构建产物生效。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_sg_universe import KEEP_TYPES, fetch_sgx_securities

_INDEX_FILE = (
    Path(__file__).resolve().parent.parent
    / "apps" / "dsa-web" / "public" / "stocks.index.json"
)

# tuple 字段顺序须与 scripts/generate_stock_index.py:compress_index 一致：
# [canonicalCode, displayCode, nameZh, pinyinFull, pinyinAbbr, aliases, market, assetType, active, popularity]
_SG_POPULARITY = 30  # 低于 A股龙头，避免抢占同名搜索靠前位


def build_sg_tuples(records: list[dict]) -> list[list]:
    out: list[list] = []
    seen: set[str] = set()
    for r in records:
        if r.get("type") not in KEEP_TYPES:
            continue
        code = (r.get("nc") or "").strip().upper()
        name = (r.get("n") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        canonical = f"{code}.SI"
        aliases = [name] if name else []
        out.append([
            canonical,        # canonicalCode（提交给后端的代码）
            code,             # displayCode
            name or code,     # nameZh（SGX 提供英文名）
            "",               # pinyinFull（SG 无拼音）
            "",               # pinyinAbbr
            aliases,          # aliases
            "SG",             # market
            "stock",          # assetType
            True,             # active
            _SG_POPULARITY,   # popularity
        ])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="把 SGX 主板股票补进前端自动补全索引")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    try:
        index = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 读取索引失败 {_INDEX_FILE}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(index, list):
        print("[ERROR] 索引不是预期的数组格式", file=sys.stderr)
        return 1

    existing = {row[0] for row in index if isinstance(row, list) and row}
    sg_before = sum(1 for row in index if isinstance(row, list) and len(row) > 6 and row[6] == "SG")

    try:
        records = fetch_sgx_securities()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 拉取 SGX 列表失败：{exc}", file=sys.stderr)
        return 1

    sg_tuples = build_sg_tuples(records)
    new_rows = [t for t in sg_tuples if t[0] not in existing]
    print(f"SGX 主板股票 {len(sg_tuples)} 只；索引中已有 SG {sg_before} 条；本次将追加 {len(new_rows)} 条。")

    if args.dry_run:
        return 0

    index.extend(new_rows)
    _INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"已写入 {_INDEX_FILE}（总计 {len(index)} 条）。记得重建前端：cd apps/dsa-web && npm run build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

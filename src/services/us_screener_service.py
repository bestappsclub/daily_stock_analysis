# -*- coding: utf-8 -*-
"""多市场（美股 / 新加坡）原生选股服务。

市场无关的选股器：扫描有界股票池，复用 ``StockTrendAnalyzer`` 打分、按策略排序，
可选 LLM 重排与 DSA 增强，返回与 ``AlphaSiftService.screen()`` **同结构**的结果，
使现有「选股」页 / ``/screen`` API 无需改动即可展示美股 / 新加坡候选。

目前覆盖市场（均通过 yfinance 批量抓取日线）：
- ``us``：默认池标普 Composite 1500（``src/data/us_universe.txt``）
- ``sg``：默认池新加坡 STI 成分股（``src/data/sg_universe.txt``，``.SI`` 形式）

设计原则（与仓库护栏一致）：
- 单只标的数据/分析失败跳过，不中断整体（fail-open）。
- LLM 重排、DSA 增强默认可降级：失败即回退为纯因子排序。
- 不触碰 A 股 / AlphaSift 链路，独立实现。

配置（环境变量，``<PREFIX>`` 为 ``US_SCREEN`` 或 ``SG_SCREEN``，见 .env.example）：
``<PREFIX>_ENABLED`` / ``<PREFIX>_UNIVERSE`` / ``<PREFIX>_UNIVERSE_FILE`` /
``<PREFIX>_MAX_UNIVERSE`` / ``<PREFIX>_HISTORY_DAYS`` / ``<PREFIX>_LLM_RERANK`` /
``<PREFIX>_LLM_RERANK_TOP`` / ``<PREFIX>_ENRICH``。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from src.config import Config, get_config
from src.stock_analyzer import (
    StockTrendAnalyzer,
    TrendAnalysisResult,
    TrendStatus,
    BuySignal,
    VolumeStatus,
    RSIStatus,
)
from data_provider.yfinance_fetcher import batch_download_us_daily
from src.services.alphasift_service import (
    _normalize_candidate,
    _enrich_candidates_with_dsa,
)

logger = logging.getLogger(__name__)

VERSION = "0.2.0"

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 每个市场的配置（env 前缀、默认股票池文件、最大默认上限）
_MARKETS: Dict[str, Dict[str, Any]] = {
    "us": {"env_prefix": "US_SCREEN", "universe_file": "us_universe.txt", "default_max": 1500, "label": "美股"},
    "sg": {"env_prefix": "SG_SCREEN", "universe_file": "sg_universe.txt", "default_max": 200, "label": "新加坡"},
}
SUPPORTED_MARKETS = tuple(_MARKETS.keys())

# 策略模板（按市场生成带前缀的 id，如 us_momentum / sg_momentum）
_STRATEGY_TEMPLATES: List[Dict[str, Any]] = [
    {"suffix": "momentum", "name": "趋势动量", "description": "综合评分高、趋势向上的标的（动量优先）。", "category": "momentum", "tags": ["动量", "趋势"]},
    {"suffix": "breakout", "name": "放量突破", "description": "放量上涨且出现买入信号的突破型标的。", "category": "breakout", "tags": ["放量", "突破"]},
    {"suffix": "oversold", "name": "超跌反转", "description": "RSI 超卖、具备反转潜力的标的。", "category": "reversal", "tags": ["超跌", "反转"]},
    {"suffix": "trend_quality", "name": "多头趋势", "description": "MA5>MA10>MA20 多头排列、趋势质量高的标的。", "category": "trend", "tags": ["多头", "趋势质量"]},
]


def strategies_for_market(market: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"{market}_{t['suffix']}",
            "name": t["name"],
            "title": t["name"],
            "description": t["description"],
            "category": t["category"],
            "tags": list(t["tags"]),
            "market_scope": [market],
            "market": market,
        }
        for t in _STRATEGY_TEMPLATES
    ]


# ----------------------------- 配置读取 -----------------------------

def _env(key: str, default: str = "") -> str:
    return (os.getenv(key, default) or "").strip()


def _env_bool(key: str, default: bool) -> bool:
    val = _env(key).lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except (TypeError, ValueError):
        return default


def is_screen_enabled(market: str) -> bool:
    cfg = _MARKETS.get(market)
    if not cfg:
        return False
    return _env_bool(f"{cfg['env_prefix']}_ENABLED", True)


def is_us_screen_enabled() -> bool:  # backward-compat
    return is_screen_enabled("us")


# ----------------------------- 服务 -----------------------------

class MarketScreenerService:
    """多市场原生选股服务（us / sg），接口与 AlphaSiftService 对齐。"""

    def __init__(self, market: str = "us", config: Optional[Config] = None):
        if market not in _MARKETS:
            raise HTTPException(
                status_code=400,
                detail={"error": "screen_unsupported_market", "message": f"不支持的选股市场：{market}"},
            )
        self.market = market
        self._cfg = _MARKETS[market]
        self._prefix = self._cfg["env_prefix"]
        self.config = config or get_config()
        self.analyzer = StockTrendAnalyzer()

    # --- 状态与策略 ---
    def status(self) -> Dict[str, Any]:
        strategies = strategies_for_market(self.market)
        return {
            "available": True,
            "enabled": is_screen_enabled(self.market),
            "version": VERSION,
            "strategy_count": len(strategies),
            "supported_markets": [self.market],
            "contract_version": "1",
        }

    def strategies(self) -> Dict[str, Any]:
        strategies = strategies_for_market(self.market)
        return {
            "enabled": is_screen_enabled(self.market),
            "available": True,
            "strategies": strategies,
            "strategy_count": len(strategies),
        }

    # --- 选股主流程 ---
    def screen(self, *, strategy: str, market: str, max_results: int) -> Dict[str, Any]:
        if not is_screen_enabled(self.market):
            raise HTTPException(
                status_code=403,
                detail={"error": "screen_disabled", "message": f"{self._cfg['label']}选股已禁用（{self._prefix}_ENABLED=false）。"},
            )
        valid_ids = {s["id"] for s in strategies_for_market(self.market)}
        if strategy not in valid_ids:
            raise HTTPException(
                status_code=400,
                detail={"error": "screen_invalid_strategy", "message": f"未知策略：{strategy}。可选：{sorted(valid_ids)}"},
            )

        warnings: List[str] = []
        universe = self._load_universe()
        if not universe:
            raise HTTPException(
                status_code=424,
                detail={"error": "screen_no_universe", "message": f"{self._cfg['label']}股票池为空，请检查 {self._prefix}_UNIVERSE(_FILE) 配置。"},
            )

        history_days = _env_int(f"{self._prefix}_HISTORY_DAYS", 150)
        try:
            frames = batch_download_us_daily(universe, days=history_days)
        except Exception as exc:  # noqa: BLE001 - 数据层失败需可降级提示
            raise HTTPException(
                status_code=424,
                detail={"error": "screen_data_failed", "message": f"{self._cfg['label']}行情批量获取失败：{exc}"},
            ) from exc

        if not frames:
            raise HTTPException(
                status_code=424,
                detail={"error": "screen_no_data", "message": f"未获取到任何{self._cfg['label']}行情数据（数据源可能限流，请稍后重试）。"},
            )
        if len(frames) < len(universe):
            warnings.append(f"{len(universe) - len(frames)}/{len(universe)} 只标的无可用行情，已跳过。")

        scored: List[TrendAnalysisResult] = []
        for code, df in frames.items():
            try:
                scored.append(self.analyzer.analyze(df, code))
            except Exception as exc:  # noqa: BLE001 - 单只分析失败跳过
                logger.debug("Screener 趋势分析失败 %s: %s", code, exc)

        suffix = strategy[len(self.market) + 1:]  # 去掉 "us_"/"sg_" 前缀
        ranked = self._apply_strategy(suffix, scored)
        top = ranked[: max(max_results, 1)]
        candidates_raw = [self._to_candidate_dict(tr, rank=i + 1) for i, tr in enumerate(top)]

        # LLM 重排（可降级）
        llm_meta: Dict[str, Any] = {}
        llm_ranked = False
        if _env_bool(f"{self._prefix}_LLM_RERANK", True) and candidates_raw:
            candidates_raw, llm_meta, llm_warnings = self._llm_rerank(candidates_raw, strategy)
            warnings.extend(llm_warnings)
            llm_ranked = bool(llm_meta)

        candidates = [_normalize_candidate(c, i + 1) for i, c in enumerate(candidates_raw)]

        # DSA 增强（默认关闭，可降级）
        dsa_enrichment: Dict[str, Any] = {"enabled": False}
        if _env_bool(f"{self._prefix}_ENRICH", False) and candidates:
            try:
                candidates, dsa_enrichment = _enrich_candidates_with_dsa(candidates)
            except Exception as exc:  # noqa: BLE001 - 增强失败不影响候选输出
                warnings.append(f"DSA 增强失败：{exc}")
                logger.warning("Screener DSA enrichment failed: %s", exc)

        return {
            "enabled": True,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "run_id": None,
            "strategy": strategy,
            "market": self.market,
            "snapshot_count": len(frames),
            "snapshot_source": "yfinance",
            "after_filter_count": len(ranked),
            "llm_ranked": llm_ranked,
            "llm_market_view": llm_meta.get("market_view", ""),
            "llm_selection_logic": llm_meta.get("selection_logic", ""),
            "llm_portfolio_risk": llm_meta.get("portfolio_risk", ""),
            "llm_coverage": llm_meta.get("coverage"),
            "llm_parse_errors": llm_meta.get("parse_errors", []),
            "warnings": warnings,
            "source_errors": [],
            "dsa_enrichment": dsa_enrichment,
        }

    # --- 股票池 ---
    def _load_universe(self) -> List[str]:
        inline = _env(f"{self._prefix}_UNIVERSE")
        symbols: List[str] = []
        if inline:
            symbols = [s.strip().upper() for s in inline.split(",") if s.strip()]
        else:
            default_file = _DATA_DIR / self._cfg["universe_file"]
            path = Path(_env(f"{self._prefix}_UNIVERSE_FILE") or str(default_file))
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        symbols.append(line.upper())
            except OSError as exc:
                logger.warning("Screener 读取股票池文件失败 %s: %s", path, exc)
                return []
        # 去重保序 + 上限
        seen: set = set()
        ordered = [s for s in symbols if not (s in seen or seen.add(s))]
        cap = _env_int(f"{self._prefix}_MAX_UNIVERSE", self._cfg["default_max"])
        if cap > 0:
            ordered = ordered[:cap]
        return ordered

    # --- 策略排序 ---
    @staticmethod
    def _apply_strategy(suffix: str, scored: List[TrendAnalysisResult]) -> List[TrendAnalysisResult]:
        if not scored:
            return []
        bullish = {TrendStatus.STRONG_BULL, TrendStatus.BULL, TrendStatus.WEAK_BULL}
        buy_signals = {BuySignal.BUY, BuySignal.STRONG_BUY}

        if suffix == "breakout":
            filtered = [
                t for t in scored
                if t.volume_status == VolumeStatus.HEAVY_VOLUME_UP and t.buy_signal in buy_signals
            ]
            key = lambda t: (t.signal_score, t.volume_ratio_5d)  # noqa: E731
        elif suffix == "oversold":
            filtered = [t for t in scored if t.rsi_status == RSIStatus.OVERSOLD]
            key = lambda t: (-t.rsi_12, t.signal_score)  # noqa: E731
        elif suffix == "trend_quality":
            filtered = [t for t in scored if t.trend_status in {TrendStatus.STRONG_BULL, TrendStatus.BULL}]
            key = lambda t: (t.trend_strength, t.signal_score)  # noqa: E731
        else:  # momentum（默认）
            filtered = [t for t in scored if t.trend_status in bullish]
            key = lambda t: (t.signal_score, t.trend_strength)  # noqa: E731

        # 命中为空则降级为对全集排序，避免返回空结果
        pool = filtered if filtered else scored
        return sorted(pool, key=key, reverse=True)

    # --- 候选构造 ---
    @staticmethod
    def _to_candidate_dict(tr: TrendAnalysisResult, rank: int) -> Dict[str, Any]:
        reasons = list(tr.signal_reasons or [])
        reason = f"{tr.trend_status.value} ｜ 评分 {tr.signal_score} ｜ {tr.buy_signal.value}"
        if reasons:
            reason += "：" + "；".join(reasons[:3])
        return {
            "rank": rank,
            "code": tr.code,
            "name": "",
            "score": tr.signal_score,
            "screen_score": tr.signal_score,
            "reason": reason,
            "risk_level": "",
            "risk_flags": list(tr.risk_factors or []),
            "price": round(tr.current_price, 4) if tr.current_price else None,
            "factor_scores": {
                "signal_score": tr.signal_score,
                "trend_strength": round(tr.trend_strength, 2),
                "bias_ma5": round(tr.bias_ma5, 2),
                "volume_ratio_5d": round(tr.volume_ratio_5d, 2),
                "rsi_12": round(tr.rsi_12, 2),
            },
            "industry": "",
            "raw": tr.to_dict(),
        }

    # --- LLM 重排 ---
    def _llm_rerank(
        self, candidates: List[Dict[str, Any]], strategy: str
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
        """对 Top-N 候选做 LLM 重排，产出 llm_* 字段。失败返回原候选 + 空 meta。"""
        warnings: List[str] = []
        try:
            from src.agent.llm_adapter import LLMToolAdapter
            adapter = LLMToolAdapter(self.config)
            # NOTE: LLMToolAdapter.is_available 是 @property，不是方法。
            if not adapter.is_available:
                return candidates, {}, ["LLM 未配置，跳过重排（仅因子排序）。"]

            top_n = _env_int(f"{self._prefix}_LLM_RERANK_TOP", 15)
            subset = candidates[:top_n]
            lines = []
            for c in subset:
                fs = c.get("factor_scores", {})
                lines.append(
                    f"{c['code']}: 评分{c.get('score')} 趋势强度{fs.get('trend_strength')} "
                    f"RSI12 {fs.get('rsi_12')} 量比{fs.get('volume_ratio_5d')} 现价{c.get('price')}"
                )
            strategies = strategies_for_market(self.market)
            strat_name = next((s["name"] for s in strategies if s["id"] == strategy), strategy)
            market_label = self._cfg["label"]
            prompt = (
                f"你是{market_label}选股分析师。下面是按「{strat_name}」策略初筛出的候选{market_label}股票及其技术指标，"
                f"请结合{market_label}市场常识做综合判断并重排。只能从给定代码中选择，不要新增代码。\n\n"
                + "\n".join(lines)
                + "\n\n严格输出 JSON（不要额外文字）：\n"
                '{"market_view":"一句话市场观点","selection_logic":"选股逻辑",'
                '"portfolio_risk":"组合风险提示","picks":[{"code":"代码","llm_score":85,'
                '"llm_sector":"行业","llm_thesis":"看多理由","llm_catalysts":["催化1"],'
                '"llm_risks":["风险1"]}]}'
            )
            resp = adapter.call_text(
                [
                    {"role": "system", "content": f"你是严谨的{market_label}选股分析师，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
                timeout=90,
            )
            text = getattr(resp, "content", "") or ""
            data = _parse_llm_json(text)
            if not isinstance(data, dict):
                return candidates, {}, ["LLM 重排返回无法解析，已回退因子排序。"]

            picks = data.get("picks") if isinstance(data.get("picks"), list) else []
            llm_by_code: Dict[str, Dict[str, Any]] = {}
            for p in picks:
                if isinstance(p, dict) and p.get("code"):
                    llm_by_code[str(p["code"]).strip().upper()] = p

            for c in candidates:
                p = llm_by_code.get(str(c.get("code", "")).strip().upper())
                if not p:
                    continue
                c["llm_score"] = p.get("llm_score")
                c["llm_sector"] = p.get("llm_sector") or ""
                c["llm_thesis"] = p.get("llm_thesis") or ""
                c["llm_catalysts"] = p.get("llm_catalysts") or []
                c["llm_risks"] = p.get("llm_risks") or []
                c["llm_theme"] = p.get("llm_theme") or ""
                c["llm_tags"] = p.get("llm_tags") or []

            order = {code: i for i, code in enumerate(llm_by_code.keys())}
            candidates.sort(key=lambda c: order.get(str(c.get("code", "")).upper(), 10_000))
            for i, c in enumerate(candidates):
                c["rank"] = i + 1

            meta = {
                "market_view": str(data.get("market_view") or ""),
                "selection_logic": str(data.get("selection_logic") or ""),
                "portfolio_risk": str(data.get("portfolio_risk") or ""),
                "coverage": len(llm_by_code),
                "parse_errors": [],
            }
            return candidates, meta, warnings
        except Exception as exc:  # noqa: BLE001 - LLM 重排失败必须可降级
            logger.warning("Screener LLM 重排失败，回退因子排序: %s", exc)
            return candidates, {}, [f"LLM 重排失败，已回退因子排序：{exc}"]


class USScreenerService(MarketScreenerService):
    """美股选股服务（backward-compatible 封装，固定 market=us）。"""

    def __init__(self, config: Optional[Config] = None):
        super().__init__("us", config=config)


# backward-compat：保留 US_STRATEGIES 导出
US_STRATEGIES = strategies_for_market("us")


def _parse_llm_json(text: str) -> Any:
    """容错解析 LLM 返回的 JSON（去掉 ```json 包裹，优先 json_repair）。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start: end + 1]
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001
        try:
            from json_repair import repair_json
            return json.loads(repair_json(cleaned))
        except Exception:  # noqa: BLE001
            return None

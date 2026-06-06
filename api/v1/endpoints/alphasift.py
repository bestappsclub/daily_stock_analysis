# -*- coding: utf-8 -*-
"""AlphaSift stock screening API routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_config_dep
from src.config import Config
from src.services.alphasift_service import AlphaSiftService
from src.services.us_screener_service import MarketScreenerService, SUPPORTED_MARKETS

router = APIRouter()


def _native_screen_market(market: str) -> str:
    """返回原生选股市场（us/sg），非原生市场返回空串（走 AlphaSift）。"""
    m = (market or "").strip().lower()
    return m if m in SUPPORTED_MARKETS else ""


class AlphaSiftScreenRequest(BaseModel):
    market: str = Field("cn", min_length=1, max_length=16)
    strategy: str = Field("dual_low", min_length=1, max_length=64)
    max_results: int = Field(20, ge=1, le=100)


class SyncCacheRequest(BaseModel):
    market: str = Field(..., min_length=1, max_length=16)
    full: bool = Field(False, description="忽略新鲜度全部重抓")


class AlphaSiftStrategyResponse(BaseModel):
    id: str
    name: str = ""
    title: str = ""
    description: str = ""
    category: str = ""
    tag: str = ""
    tags: List[str] = Field(default_factory=list)
    market_scope: List[str] = Field(default_factory=list)
    market: str = ""


def _service(config: Config) -> AlphaSiftService:
    return AlphaSiftService(config=config)


@router.get("/status")
def alphasift_status(
    market: str = "cn",
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    native = _native_screen_market(market)
    if native:
        return MarketScreenerService(native, config=config).status()
    return _service(config).status()


@router.get("/strategies")
def alphasift_strategies(
    request: Request,
    market: str = "cn",
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    native = _native_screen_market(market)
    if native:
        return MarketScreenerService(native, config=config).strategies()
    return _service(config).strategies()


@router.post("/install")
def alphasift_install(
    request: Request,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    # 安装仅适用于 A 股 AlphaSift 引擎；美股为原生能力，无需安装。
    return _service(config).install(request=request)


@router.post("/screen")
def alphasift_screen(
    request: AlphaSiftScreenRequest,
    http_request: Request,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    native = _native_screen_market(request.market)
    if native:
        return MarketScreenerService(native, config=config).screen(
            strategy=request.strategy,
            market=request.market,
            max_results=request.max_results,
        )
    return _service(config).screen(
        strategy=request.strategy,
        market=request.market,
        max_results=request.max_results,
    )


@router.post("/sync-cache")
def alphasift_sync_cache(
    request: SyncCacheRequest,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    """同步本地行情缓存（仅原生市场 us/sg；A 股走 AlphaSift，无本地行情缓存）。"""
    native = _native_screen_market(request.market)
    if not native:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "sync_unsupported_market",
                "message": f"仅美股/新加坡支持本地行情缓存同步（market={request.market}）",
            },
        )
    return MarketScreenerService(native, config=config).sync_cache(full=request.full)

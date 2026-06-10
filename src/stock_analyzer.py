# -*- coding: utf-8 -*-
"""
===================================
趋势交易分析器 - 基于用户交易理念
===================================

交易理念核心原则：
1. 严进策略 - 不追高，追求每笔交易成功率
2. 趋势交易 - MA5>MA10>MA20 多头排列，顺势而为
3. 效率优先 - 关注筹码结构好的股票
4. 买点偏好 - 在 MA5/MA10 附近回踩买入

技术标准：
- 多头排列：MA5 > MA10 > MA20
- 乖离率：(Close - MA5) / MA5 < 5%（不追高）
- 量能形态：缩量回调优先
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum

import pandas as pd
import numpy as np

from src.config import get_config

logger = logging.getLogger(__name__)


class TrendStatus(Enum):
    """趋势状态枚举"""
    STRONG_BULL = "强势多头"      # MA5 > MA10 > MA20，且间距扩大
    BULL = "多头排列"             # MA5 > MA10 > MA20
    WEAK_BULL = "弱势多头"        # MA5 > MA10，但 MA10 < MA20
    CONSOLIDATION = "盘整"        # 均线缠绕
    WEAK_BEAR = "弱势空头"        # MA5 < MA10，但 MA10 > MA20
    BEAR = "空头排列"             # MA5 < MA10 < MA20
    STRONG_BEAR = "强势空头"      # MA5 < MA10 < MA20，且间距扩大


class VolumeStatus(Enum):
    """量能状态枚举"""
    HEAVY_VOLUME_UP = "放量上涨"       # 量价齐升
    HEAVY_VOLUME_DOWN = "放量下跌"     # 放量杀跌
    SHRINK_VOLUME_UP = "缩量上涨"      # 无量上涨
    SHRINK_VOLUME_DOWN = "缩量回调"    # 缩量回调（好）
    NORMAL = "量能正常"


class BuySignal(Enum):
    """买入信号枚举"""
    STRONG_BUY = "强烈买入"       # 多条件满足
    BUY = "买入"                  # 基本条件满足
    HOLD = "持有"                 # 已持有可继续
    WAIT = "观望"                 # 等待更好时机
    SELL = "卖出"                 # 趋势转弱
    STRONG_SELL = "强烈卖出"      # 趋势破坏


class MACDStatus(Enum):
    """MACD状态枚举"""
    GOLDEN_CROSS_ZERO = "零轴上金叉"      # DIF上穿DEA，且在零轴上方
    GOLDEN_CROSS = "金叉"                # DIF上穿DEA
    BULLISH = "多头"                    # DIF>DEA>0
    CROSSING_UP = "上穿零轴"             # DIF上穿零轴
    CROSSING_DOWN = "下穿零轴"           # DIF下穿零轴
    BEARISH = "空头"                    # DIF<DEA<0
    DEATH_CROSS = "死叉"                # DIF下穿DEA


class RSIStatus(Enum):
    """RSI状态枚举"""
    OVERBOUGHT = "超买"        # RSI > 70
    STRONG_BUY = "强势买入"    # 50 < RSI < 70
    NEUTRAL = "中性"          # 40 <= RSI <= 60
    WEAK = "弱势"             # 30 < RSI < 40
    OVERSOLD = "超卖"         # RSI < 30


@dataclass
class TrendAnalysisResult:
    """趋势分析结果"""
    code: str
    
    # 趋势判断
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""           # 均线排列描述
    trend_strength: float = 0.0      # 趋势强度 0-100
    
    # 均线数据
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    current_price: float = 0.0
    
    # 乖离率（与 MA5 的偏离度）
    bias_ma5: float = 0.0            # (Close - MA5) / MA5 * 100
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0
    
    # 量能分析
    volume_status: VolumeStatus = VolumeStatus.NORMAL
    volume_ratio_5d: float = 0.0     # 当日成交量/5日均量
    volume_trend: str = ""           # 量能趋势描述
    
    # 支撑压力
    support_ma5: bool = False        # MA5 是否构成支撑
    support_ma10: bool = False       # MA10 是否构成支撑
    resistance_levels: List[float] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)

    # MACD 指标
    macd_dif: float = 0.0          # DIF 快线
    macd_dea: float = 0.0          # DEA 慢线
    macd_bar: float = 0.0           # MACD 柱状图
    macd_status: MACDStatus = MACDStatus.BULLISH
    macd_signal: str = ""            # MACD 信号描述

    # RSI 指标
    rsi_6: float = 0.0              # RSI(6) 短期
    rsi_12: float = 0.0             # RSI(12) 中期
    rsi_24: float = 0.0             # RSI(24) 长期
    rsi_status: RSIStatus = RSIStatus.NEUTRAL
    rsi_signal: str = ""              # RSI 信号描述

    # 买入信号
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0            # 综合评分 0-100
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)

    # 摆动结构（道氏理论：头头高底底高=多头 / 头头低底底低=空头）
    structure: str = "unknown"       # "bull" | "bear" | "range" | "unknown"
    structure_desc: str = ""         # 人类可读描述

    # 东财式 DK 买卖点状态（价格突破 + 放量折扣状态机，详见 _analyze_dk）
    dk_state: str = "unknown"        # "hold"(持股/多) | "cash"(持币/空) | "unknown"
    dk_signal: str = ""              # 仅当最新一根就是翻转点时为 "D"/"K"（=当天出现），否则 ""
    dk_last_signal: str = ""         # 最近一次翻转点类型 "D"/"K"（不论几天前），无则 ""
    dk_days_since: int = -1          # 距最近一次 D/K 翻转点的交易日数（0=当天，-1=无）
    dk_desc: str = ""                # 人类可读描述

    # 跳空缺口（开盘相对昨收）：最近一次显著缺口的方向/幅度/几天前
    gap_dir: str = ""                # "up"(向上跳空) | "down"(向下跳空) | ""
    gap_pct: float = 0.0             # 该缺口幅度（带符号，%）
    gap_days_since: int = -1         # 距最近缺口的交易日数（0=当天，-1=无）
    gap_desc: str = ""               # 人类可读描述

    # 出场/止损位（与 stockscreener 一致）
    chandelier_stop: float = 0.0     # 吊灯止损价（多头止损线，最新一根）
    chandelier_dir: int = 0          # 吊灯方向：1=多 / -1=空 / 0=未知
    dk_trail_stop: float = 0.0       # DK 持仓 12% 移动止损价（仅持股态有意义，否则 0）
    exit_desc: str = ""              # 止损位人类可读描述

    # ADX / DMI（趋势强度：判断"趋势 or 震荡"，决定该不该用趋势类策略）
    adx: float = 0.0                 # ADX 值（越大趋势越强）
    plus_di: float = 0.0             # +DI（多头动向）
    minus_di: float = 0.0            # -DI（空头动向）
    adx_status: str = "unknown"      # "strong_trend" | "trend" | "range" | "unknown"
    adx_desc: str = ""               # 人类可读描述

    # OBV 量价确认 / 背离（价涨量是否跟上，滤假突破）
    obv: float = 0.0                 # 最新 OBV 值（能量潮，累积量）
    obv_trend: str = ""              # "up" | "down" | "flat"（OBV 近窗口方向）
    obv_divergence: str = ""         # "bullish"(底背离) | "bearish"(顶背离) | ""
    vol_confirm_desc: str = ""       # 人类可读描述（量价配合/背离）

    # 相对强弱 RS（个股 vs 大盘指数；需 benchmark，缺失则中性）
    rs_ratio: float = 0.0            # (1+个股收益)/(1+大盘收益)，>1=跑赢，0=无数据
    rs_chg_pct: float = 0.0          # 相对大盘超额收益（百分点，正=跑赢）
    rs_status: str = "neutral"       # "leading"(领先) | "lagging"(落后) | "neutral"
    rs_desc: str = ""                # 人类可读描述

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'trend_status': self.trend_status.value,
            'ma_alignment': self.ma_alignment,
            'trend_strength': self.trend_strength,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'ma60': self.ma60,
            'current_price': self.current_price,
            'bias_ma5': self.bias_ma5,
            'bias_ma10': self.bias_ma10,
            'bias_ma20': self.bias_ma20,
            'volume_status': self.volume_status.value,
            'volume_ratio_5d': self.volume_ratio_5d,
            'volume_trend': self.volume_trend,
            'support_ma5': self.support_ma5,
            'support_ma10': self.support_ma10,
            'buy_signal': self.buy_signal.value,
            'signal_score': self.signal_score,
            'signal_reasons': self.signal_reasons,
            'risk_factors': self.risk_factors,
            'macd_dif': self.macd_dif,
            'macd_dea': self.macd_dea,
            'macd_bar': self.macd_bar,
            'macd_status': self.macd_status.value,
            'macd_signal': self.macd_signal,
            'rsi_6': self.rsi_6,
            'rsi_12': self.rsi_12,
            'rsi_24': self.rsi_24,
            'rsi_status': self.rsi_status.value,
            'rsi_signal': self.rsi_signal,
            'structure': self.structure,
            'structure_desc': self.structure_desc,
            'dk_state': self.dk_state,
            'dk_signal': self.dk_signal,
            'dk_last_signal': self.dk_last_signal,
            'dk_days_since': self.dk_days_since,
            'dk_desc': self.dk_desc,
            'gap_dir': self.gap_dir,
            'gap_pct': self.gap_pct,
            'gap_days_since': self.gap_days_since,
            'gap_desc': self.gap_desc,
            'chandelier_stop': self.chandelier_stop,
            'chandelier_dir': self.chandelier_dir,
            'dk_trail_stop': self.dk_trail_stop,
            'exit_desc': self.exit_desc,
            'adx': self.adx,
            'plus_di': self.plus_di,
            'minus_di': self.minus_di,
            'adx_status': self.adx_status,
            'adx_desc': self.adx_desc,
            'obv': self.obv,
            'obv_trend': self.obv_trend,
            'obv_divergence': self.obv_divergence,
            'vol_confirm_desc': self.vol_confirm_desc,
            'rs_ratio': self.rs_ratio,
            'rs_chg_pct': self.rs_chg_pct,
            'rs_status': self.rs_status,
            'rs_desc': self.rs_desc,
        }


class StockTrendAnalyzer:
    """
    股票趋势分析器

    基于用户交易理念实现：
    1. 趋势判断 - MA5>MA10>MA20 多头排列
    2. 乖离率检测 - 不追高，偏离 MA5 超过 5% 不买
    3. 量能分析 - 偏好缩量回调
    4. 买点识别 - 回踩 MA5/MA10 支撑
    5. MACD 指标 - 趋势确认和金叉死叉信号
    6. RSI 指标 - 超买超卖判断
    """
    
    # 交易参数配置（BIAS_THRESHOLD 从 Config 读取，见 _generate_signal）
    VOLUME_SHRINK_RATIO = 0.7   # 缩量判断阈值（当日量/5日均量）
    VOLUME_HEAVY_RATIO = 1.5    # 放量判断阈值
    MA_SUPPORT_TOLERANCE = 0.02  # MA 支撑判断容忍度（2%）

    # MACD 参数（标准12/26/9）
    MACD_FAST = 12              # 快线周期
    MACD_SLOW = 26             # 慢线周期
    MACD_SIGNAL = 9             # 信号线周期

    # RSI 参数
    RSI_SHORT = 6               # 短期RSI周期
    RSI_MID = 12               # 中期RSI周期
    RSI_LONG = 24              # 长期RSI周期
    RSI_OVERBOUGHT = 70        # 超买阈值
    RSI_OVERSOLD = 30          # 超卖阈值

    # 摆动结构识别窗口 N：某点比左右各 N 根都高/低才算摆动高/低点（可经 SWING_PIVOT_WINDOW 覆盖）
    SWING_WINDOW = int(os.getenv("SWING_PIVOT_WINDOW", "3") or 3)

    # 东财式 DK 买卖点参数（与 stockscreener technical.py:_dk_buysell_state 保持一致，
    # 东财校准版：收盘价唐奇安通道 + 短周期软突破 + 放量延续，不用 high/low 与固定折扣）
    DK_NUP = int(os.getenv("DK_NUP", "20") or 20)        # 硬突破位窗口 HHV(close, N)，不含当日
    DK_NSOFT = int(os.getenv("DK_NSOFT", "7") or 7)      # 放量软突破窗口 HHV(close, N)，不含当日
    DK_NDN = int(os.getenv("DK_NDN", "10") or 10)        # 破位窗口 LLV(close, N)，不含当日
    DK_VWIN = int(os.getenv("DK_VWIN", "20") or 20)      # 放量阈值均量窗口
    DK_VMULT = float(os.getenv("DK_VMULT", "1.0") or 1.0)  # 放量阈值倍数 × MA(VOL, vwin)

    # 跳空缺口参数：开盘相对昨收的缺口幅度阈值（百分比）与"最近一周"窗口（交易日）
    GAP_MIN_PCT = float(os.getenv("GAP_MIN_PCT", "1.0") or 1.0)   # 缺口最小幅度(%)
    GAP_WINDOW = int(os.getenv("GAP_WINDOW", "5") or 5)          # 近 N 个交易日内算"最近缺口"

    # 出场/止损（与 stockscreener 一致）：吊灯止损 + DK 持仓 12% 移动止损
    CHANDELIER_LEN = int(os.getenv("CHANDELIER_LEN", "22") or 22)        # 吊灯止损窗口
    CHANDELIER_MULT = float(os.getenv("CHANDELIER_MULT", "3.0") or 3.0)  # ATR 倍数
    DK_TRAIL_PCT = float(os.getenv("DK_TRAIL_PCT", "0.12") or 0.12)      # DK 持仓移动止损回撤比例

    # ADX / DMI（Wilder 平滑）：判断趋势强度，ADX≥TREND_MIN 视为有趋势，≥STRONG 视为强趋势
    ADX_LEN = int(os.getenv("ADX_LEN", "14") or 14)                     # ADX/DI 平滑周期
    ADX_TREND_MIN = float(os.getenv("ADX_TREND_MIN", "25") or 25)       # 趋势确认阈值
    ADX_STRONG = float(os.getenv("ADX_STRONG", "40") or 40)            # 强趋势阈值

    # OBV 量价背离检测窗口（近 N 个交易日内比较价与 OBV 的变化方向）
    OBV_DIV_WIN = int(os.getenv("OBV_DIV_WIN", "20") or 20)

    # 相对强弱 RS：与大盘指数对比的回看窗口（交易日）
    RS_LOOKBACK = int(os.getenv("RS_LOOKBACK", "60") or 60)

    def __init__(self):
        """初始化分析器"""
        pass
    
    def analyze(self, df: pd.DataFrame, code: str,
                benchmark_df: Optional[pd.DataFrame] = None) -> TrendAnalysisResult:
        """
        分析股票趋势

        Args:
            df: 包含 OHLCV 数据的 DataFrame
            code: 股票代码
            benchmark_df: 可选，大盘指数日线（需含 'date'/'close'），用于计算相对强弱 RS；
                          为 None 或数据不足时 RS 字段保持中性（fail-open，不影响其余分析）。

        Returns:
            TrendAnalysisResult 分析结果
        """
        result = TrendAnalysisResult(code=code)
        
        if df is None or df.empty or len(df) < 20:
            logger.warning(f"{code} 数据不足，无法进行趋势分析")
            result.risk_factors.append("数据不足，无法完成分析")
            return result
        
        # 确保数据按日期排序
        df = df.sort_values('date').reset_index(drop=True)
        
        # 计算均线
        df = self._calculate_mas(df)

        # 计算 MACD 和 RSI
        df = self._calculate_macd(df)
        df = self._calculate_rsi(df)

        # 获取最新数据
        latest = df.iloc[-1]
        result.current_price = float(latest['close'])
        result.ma5 = float(latest['MA5'])
        result.ma10 = float(latest['MA10'])
        result.ma20 = float(latest['MA20'])
        result.ma60 = float(latest.get('MA60', 0))

        # 1. 趋势判断
        self._analyze_trend(df, result)

        # 2. 乖离率计算
        self._calculate_bias(result)

        # 3. 量能分析
        self._analyze_volume(df, result)

        # 4. 支撑压力分析
        self._analyze_support_resistance(df, result)

        # 5. MACD 分析
        self._analyze_macd(df, result)

        # 6. RSI 分析
        self._analyze_rsi(df, result)

        # 7. 生成买入信号
        self._generate_signal(result)

        # 8. 摆动结构分析（头头高底底高 / 头头低底底低）
        try:
            self._analyze_swing_structure(df, result)
        except Exception as exc:  # noqa: BLE001 - 结构分析失败不应影响主趋势结果
            logger.debug(f"{code} 摆动结构分析失败: {exc}")

        # 9. 东财式 DK 买卖点（价格突破 + 放量折扣状态机）
        try:
            self._analyze_dk(df, result)
        except Exception as exc:  # noqa: BLE001 - DK 分析失败不应影响主趋势结果
            logger.debug(f"{code} DK 买卖点分析失败: {exc}")

        # 10. 跳空缺口（开盘相对昨收）
        try:
            self._analyze_gap(df, result)
        except Exception as exc:  # noqa: BLE001 - 缺口分析失败不应影响主趋势结果
            logger.debug(f"{code} 跳空缺口分析失败: {exc}")

        # 11. 出场/止损位（吊灯止损 + DK 12% 移动止损在 _analyze_dk 中已算）
        try:
            self._analyze_exits(df, result)
        except Exception as exc:  # noqa: BLE001 - 止损位计算失败不应影响主趋势结果
            logger.debug(f"{code} 止损位计算失败: {exc}")

        # 12. ADX / DMI（趋势强度）
        try:
            self._analyze_adx(df, result)
        except Exception as exc:  # noqa: BLE001 - ADX 计算失败不应影响主趋势结果
            logger.debug(f"{code} ADX 计算失败: {exc}")

        # 13. OBV 量价确认 / 背离
        try:
            self._analyze_obv(df, result)
        except Exception as exc:  # noqa: BLE001 - OBV 计算失败不应影响主趋势结果
            logger.debug(f"{code} OBV 计算失败: {exc}")

        # 14. 相对强弱 RS（vs 大盘指数；benchmark_df 缺失则保持中性）
        try:
            self._analyze_relative_strength(df, result, benchmark_df)
        except Exception as exc:  # noqa: BLE001 - RS 计算失败不应影响主趋势结果
            logger.debug(f"{code} 相对强弱 RS 计算失败: {exc}")

        return result

    def _analyze_exits(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """吊灯止损 Chandelier Exit（收盘价基准，棘轮只进不退）+ 汇总止损描述。

        镜像 stockscreener ``technical.py:chandelier_exit``：
            多头止损 = HHV(close, len) − mult·ATR（随新高上抬）
            空头止损 = LLV(close, len) + mult·ATR（随新低下压）
        收盘上穿空头止损 → 转多；下穿多头止损 → 转空。设置最新一根的
        ``chandelier_stop``/``chandelier_dir``，并与 DK 12% 移动止损合成 ``exit_desc``。
        """
        length = max(self.CHANDELIER_LEN, 1)
        mult = self.CHANDELIER_MULT
        n = len(df)
        if n < length + 1 or 'high' not in df.columns or 'low' not in df.columns:
            return
        h = df['high'].to_numpy(dtype=float)
        low = df['low'].to_numpy(dtype=float)
        c = df['close'].to_numpy(dtype=float)
        # ATR（Wilder 近似，与 stockscreener chandelier_exit 一致）
        atr = [0.0] * n
        for i in range(1, n):
            tr = max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1]))
            atr[i] = (atr[i - 1] * (i - 1) + tr) / i if i < length else (atr[i - 1] * (length - 1) + tr) / length
        import numpy as _np
        hhv = pd.Series(c).rolling(length).max().to_numpy()
        llv = pd.Series(c).rolling(length).min().to_numpy()
        long_stop = [float('nan')] * n
        short_stop = [float('nan')] * n
        direction = 1
        for i in range(length, n):
            a = hhv[i] - mult * atr[i]
            b = llv[i] + mult * atr[i]
            if i > length and not _np.isnan(long_stop[i - 1]):
                a = max(a, long_stop[i - 1]) if c[i - 1] > long_stop[i - 1] else a
                b = min(b, short_stop[i - 1]) if c[i - 1] < short_stop[i - 1] else b
            long_stop[i] = a
            short_stop[i] = b
            if i > length:
                if c[i] > short_stop[i - 1]:
                    direction = 1
                elif c[i] < long_stop[i - 1]:
                    direction = -1
        result.chandelier_dir = direction
        result.chandelier_stop = round(long_stop[-1] if direction == 1 else short_stop[-1], 4)

        parts = []
        if result.chandelier_stop:
            parts.append(f"吊灯{'多' if direction == 1 else '空'} {result.chandelier_stop:g}")
        if result.dk_trail_stop:
            parts.append(f"移止12% {result.dk_trail_stop:g}")
        result.exit_desc = " / ".join(parts)

    def _analyze_adx(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """ADX / DMI（Wilder 平滑，与 _calculate_rsi 同口径 ewm(alpha=1/period)）。

        TR / +DM / −DM → 平滑 → +DI、−DI → DX → ADX。判断当前是趋势还是震荡：
        ADX≥ADX_TREND_MIN 视为有趋势（趋势/突破类策略才可靠），<阈值视为震荡。
        +DI>−DI 多头主导，反之空头主导。
        """
        period = max(self.ADX_LEN, 2)
        n = len(df)
        if n < period * 2 or not {'high', 'low', 'close'}.issubset(df.columns):
            return
        high = df['high'].to_numpy(dtype=float)
        low = df['low'].to_numpy(dtype=float)
        close = df['close'].to_numpy(dtype=float)
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up_move = high[i] - high[i - 1]
            down_move = low[i - 1] - low[i]
            plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

        alpha = 1.0 / period
        atr = pd.Series(tr).ewm(alpha=alpha, adjust=False).mean().to_numpy()
        sm_plus = pd.Series(plus_dm).ewm(alpha=alpha, adjust=False).mean().to_numpy()
        sm_minus = pd.Series(minus_dm).ewm(alpha=alpha, adjust=False).mean().to_numpy()
        with np.errstate(divide='ignore', invalid='ignore'):
            plus_di = 100.0 * sm_plus / atr
            minus_di = 100.0 * sm_minus / atr
            di_sum = plus_di + minus_di
            dx = 100.0 * np.abs(plus_di - minus_di) / di_sum
        dx = np.nan_to_num(dx, nan=0.0, posinf=0.0, neginf=0.0)
        adx = pd.Series(dx).ewm(alpha=alpha, adjust=False).mean().to_numpy()

        result.adx = round(float(adx[-1]), 2)
        result.plus_di = round(float(np.nan_to_num(plus_di[-1])), 2)
        result.minus_di = round(float(np.nan_to_num(minus_di[-1])), 2)
        if result.adx >= self.ADX_STRONG:
            result.adx_status = "strong_trend"
        elif result.adx >= self.ADX_TREND_MIN:
            result.adx_status = "trend"
        else:
            result.adx_status = "range"
        if result.adx_status == "range":
            result.adx_desc = f"ADX {result.adx:g} 震荡（趋势弱，趋势策略慎用）"
        else:
            direction = "多" if result.plus_di >= result.minus_di else "空"
            label = "强趋势" if result.adx_status == "strong_trend" else "趋势确认"
            result.adx_desc = (
                f"ADX {result.adx:g} {label}（{direction}向，"
                f"+DI{result.plus_di:g}/−DI{result.minus_di:g}）"
            )

    def _analyze_obv(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """OBV 能量潮 + 量价确认/背离。

        OBV：收盘上涨累加当日量、下跌减去当日量。在 OBV_DIV_WIN 窗口内比较价与 OBV
        的同期变化方向：价涨而 OBV 走弱=顶背离(bearish，追加风险标记)，
        价跌而 OBV 走强=底背离(bullish)，方向一致=量价配合。
        """
        win = max(self.OBV_DIV_WIN, 2)
        n = len(df)
        if n < win + 1 or 'volume' not in df.columns:
            return
        close = df['close'].to_numpy(dtype=float)
        vol = df['volume'].to_numpy(dtype=float)
        obv = np.zeros(n)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                obv[i] = obv[i - 1] + vol[i]
            elif close[i] < close[i - 1]:
                obv[i] = obv[i - 1] - vol[i]
            else:
                obv[i] = obv[i - 1]
        result.obv = round(float(obv[-1]), 2)

        price_chg = close[-1] - close[-1 - win]
        obv_chg = obv[-1] - obv[-1 - win]
        result.obv_trend = "up" if obv_chg > 0 else ("down" if obv_chg < 0 else "flat")

        if price_chg > 0 and obv_chg < 0:
            result.obv_divergence = "bearish"
            result.vol_confirm_desc = f"顶背离：价涨而 OBV 走弱（量价不配合，{win}日）"
            result.risk_factors.append("OBV 顶背离：上涨缺量能支撑，提防假突破")
        elif price_chg < 0 and obv_chg > 0:
            result.obv_divergence = "bullish"
            result.vol_confirm_desc = f"底背离：价跌而 OBV 走强（资金潜流入，{win}日）"
        else:
            result.obv_divergence = ""
            if price_chg > 0 and obv_chg > 0:
                result.vol_confirm_desc = f"量价配合：价涨 OBV 同步上行（{win}日）"
            elif price_chg < 0 and obv_chg < 0:
                result.vol_confirm_desc = f"量价同步走弱（{win}日）"
            else:
                result.vol_confirm_desc = ""

    def _analyze_relative_strength(
        self, df: pd.DataFrame, result: TrendAnalysisResult,
        benchmark_df: Optional[pd.DataFrame] = None,
    ) -> None:
        """相对强弱 RS（个股 vs 大盘指数）。

        benchmark_df（含 'date'/'close'）按日期对齐到个股，取 RS_LOOKBACK 窗口比较累计收益：
            rs_chg_pct = (个股收益 − 大盘收益) × 100（正=跑赢）
            rs_ratio   = (1+个股收益)/(1+大盘收益)（>1=跑赢）
        并看 RS 线（个股/大盘）是否上行。leading=跑赢且 RS 上行；lagging=跑输且 RS 下行。
        benchmark_df 缺失/对齐后数据不足时直接返回，字段保持中性（fail-open）。
        """
        if benchmark_df is None or getattr(benchmark_df, "empty", True):
            return
        if 'close' not in benchmark_df.columns or 'date' not in benchmark_df.columns:
            return
        if 'date' not in df.columns or 'close' not in df.columns:
            return
        look = max(self.RS_LOOKBACK, 2)
        bench = benchmark_df[['date', 'close']].rename(columns={'close': '_bench_close'})
        merged = df[['date', 'close']].merge(bench, on='date', how='left').dropna(subset=['_bench_close'])
        avail = len(merged)
        if avail < 21:  # 对齐后不足约 1 个月，RS 无统计意义，保持中性
            return
        look = min(look, avail - 1)  # 历史不足时收窄窗口（个股分析仅取 ~60 交易日）
        stock_close = merged['close'].to_numpy(dtype=float)
        bench_close = merged['_bench_close'].to_numpy(dtype=float)
        s0, s1 = stock_close[-1 - look], stock_close[-1]
        b0, b1 = bench_close[-1 - look], bench_close[-1]
        if s0 <= 0 or b0 <= 0:
            return
        stock_ret = s1 / s0 - 1.0
        bench_ret = b1 / b0 - 1.0
        result.rs_chg_pct = round((stock_ret - bench_ret) * 100.0, 2)
        if (1.0 + bench_ret) != 0:
            result.rs_ratio = round((1.0 + stock_ret) / (1.0 + bench_ret), 4)
        rs_line = stock_close / bench_close
        rs_rising = rs_line[-1] > rs_line[-1 - look]
        if result.rs_chg_pct > 0 and rs_rising:
            result.rs_status = "leading"
        elif result.rs_chg_pct < 0 and not rs_rising:
            result.rs_status = "lagging"
        else:
            result.rs_status = "neutral"
        sign = "+" if result.rs_chg_pct >= 0 else ""
        verb = "跑赢" if result.rs_chg_pct >= 0 else "跑输"
        arrow = "RS上行" if rs_rising else "RS走平/下行"
        result.rs_desc = f"{verb}大盘 {sign}{result.rs_chg_pct:g}%（{look}日，{arrow}）"

    def _analyze_gap(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """跳空缺口：开盘价相对昨收的缺口。从最新一根往回找**最近一次**显著缺口
        （|缺口%| ≥ GAP_MIN_PCT），记录方向/幅度/几天前（0=当天）。

        只扫到 GAP_WINDOW + 少量余量即可——策略只关心"近一周内"的缺口。
        """
        length = len(df)
        if length < 2 or 'open' not in df.columns:
            return
        open_ = df['open'].to_numpy(dtype=float)
        close = df['close'].to_numpy(dtype=float)
        thr = max(self.GAP_MIN_PCT, 0.0)
        scan = min(length - 1, max(self.GAP_WINDOW, 1) + 1)  # 最近窗口内找
        for back in range(scan):
            i = length - 1 - back
            prev_c = close[i - 1]
            if prev_c <= 0:
                continue
            g = (open_[i] - prev_c) / prev_c * 100.0
            if abs(g) >= thr:
                result.gap_dir = "up" if g > 0 else "down"
                result.gap_pct = round(g, 2)
                result.gap_days_since = back
                when = "当天" if back == 0 else f"{back} 天前"
                arrow = "向上跳空" if g > 0 else "向下跳空"
                result.gap_desc = f"{arrow} {abs(g):.1f}%（{when}）"
                return

    def _analyze_dk(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """东财式 DK 买卖点状态机（东财校准版：收盘价唐奇安通道 + 短周期软突破 + 放量延续）。

        镜像 stockscreener ``technical.py:_dk_buysell_state``（提交 90ffdb7 起，按东财
        「明日提示」逆向标定确认）：
            持股(D点) ← 持币 且 (收盘 > 硬突破位 HHV(close, n_up))
                              或 (收盘 > 放量软突破位 HHV(close, n_soft) 且 放量)
            持币(K点) ← 持股 且 (收盘 < 破位 LLV(close, n_dn))
        要点：通道**全部用收盘价**（非 high/low）；放量位是独立的**短周期**收盘高点
        （非 hard×固定系数）；放量条件含「昨日已放量延续」。硬突破/破位之间形成滞后带 →
        信号稀疏。设置最新一根的 ``dk_state``/``dk_signal`` 及最近翻转的
        ``dk_last_signal``/``dk_days_since``。

        注意：与东财对齐需前复权数据（本仓库 yfinance/akshare 抓取默认前复权）。
        """
        n_up = max(self.DK_NUP, 1)
        n_soft = max(self.DK_NSOFT, 1)
        n_dn = max(self.DK_NDN, 1)
        v_win = max(self.DK_VWIN, 1)
        length = len(df)
        if length < n_up + 1:
            result.dk_state = "unknown"
            result.dk_desc = "数据不足以计算 DK 买卖点"
            return

        close = df['close'].to_numpy(dtype=float)
        has_vol = 'volume' in df.columns
        vol = df['volume'].to_numpy(dtype=float) if has_vol else None

        def _vol_thr(end_idx: int) -> float:
            # vol_mult × MA(VOL, v_win)（含当根，end_idx<v_win-1 不足返回 nan）
            if not has_vol or end_idx < v_win - 1 or end_idx < 0:
                return float('nan')
            return float(vol[end_idx - v_win + 1: end_idx + 1].mean()) * self.DK_VMULT

        bull = False
        last_flip_idx = -1       # 最近一次状态翻转所在的 K 线下标
        last_flip_type = ""      # 该翻转类型："D"（转持股）/"K"（转持币）
        for i in range(length):
            if i >= n_up:
                hard_up = close[i - n_up:i].max()      # HHV(close, n_up)，不含当日
                soft_up = close[i - n_soft:i].max()    # HHV(close, n_soft)，不含当日
                hard_dn = close[i - n_dn:i].min()      # LLV(close, n_dn)，不含当日
                c = close[i]
                if not bull:
                    vt_i = _vol_thr(i)
                    vt_prev = _vol_thr(i - 1)
                    vol_ok = (vt_i == vt_i and vol[i] > vt_i) or \
                             (i > 0 and vt_prev == vt_prev and vol[i - 1] > vt_prev)
                    if c > hard_up or (c > soft_up and vol_ok):
                        bull = True
                        last_flip_idx, last_flip_type = i, "D"
                elif c < hard_dn:
                    bull = False
                    last_flip_idx, last_flip_type = i, "K"

        result.dk_state = "hold" if bull else "cash"
        if last_flip_idx >= 0:
            days_since = (length - 1) - last_flip_idx
            result.dk_last_signal = last_flip_type
            result.dk_days_since = days_since
            # 仅当最新一根就是翻转点时算"当天出现"
            result.dk_signal = last_flip_type if days_since == 0 else ""
            label = "D点/买点" if last_flip_type == "D" else "K点/卖点"
            when = "当天" if days_since == 0 else f"{days_since} 天前"
            holding = "持股" if bull else "持币"
            result.dk_desc = f"{holding}｜{label} {when}出现"
        else:
            result.dk_signal = ""
            result.dk_last_signal = ""
            result.dk_days_since = -1
            result.dk_desc = "持股（多头持有）" if bull else "持币（空仓观望）"

        # DK 持仓 12% 移动止损：持股态下，从 D 点进场跟踪持仓期最高收盘，止损=峰值×(1-pct)
        if bull and last_flip_type == "D" and last_flip_idx >= 0:
            peak = float(close[last_flip_idx:length].max())
            result.dk_trail_stop = round(peak * (1.0 - self.DK_TRAIL_PCT), 4)

    def _analyze_swing_structure(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """道氏摆动结构识别（头头高底底高 / 头头低底底低）。

        用 fractal 法找摆动高/低点：某根比左右各 N 根都高 -> 摆动高点(头)，
        都低 -> 摆动低点(底)。比较最近两个高点与最近两个低点：
        - 头头高 + 底底高 -> bull（多头结构）
        - 头头低 + 底底低 -> bear（空头结构）
        - 其余 -> range（震荡/背离）
        """
        n = max(int(self.SWING_WINDOW), 1)
        length = len(df)
        if length < (2 * n + 1) * 2:
            result.structure = "unknown"
            result.structure_desc = "数据不足以识别摆动结构"
            return

        highs = (df['high'] if 'high' in df.columns else df['close']).to_numpy()
        lows = (df['low'] if 'low' in df.columns else df['close']).to_numpy()

        swing_high_idx: List[int] = []
        swing_low_idx: List[int] = []
        for i in range(n, length - n):
            wh = highs[i - n:i + n + 1]
            wl = lows[i - n:i + n + 1]
            if highs[i] == wh.max() and int(wh.argmax()) == n:
                swing_high_idx.append(i)
            if lows[i] == wl.min() and int(wl.argmin()) == n:
                swing_low_idx.append(i)

        if len(swing_high_idx) < 2 or len(swing_low_idx) < 2:
            result.structure = "range"
            result.structure_desc = "摆动高/低点不足，结构未明"
            return

        higher_high = highs[swing_high_idx[-1]] > highs[swing_high_idx[-2]]
        higher_low = lows[swing_low_idx[-1]] > lows[swing_low_idx[-2]]

        if higher_high and higher_low:
            result.structure = "bull"
            result.structure_desc = "头头高 + 底底高（多头结构）"
        elif (not higher_high) and (not higher_low):
            result.structure = "bear"
            result.structure_desc = "头头低 + 底底低（空头结构）"
        else:
            result.structure = "range"
            result.structure_desc = (
                "头头高但底底低（背离/震荡）" if higher_high else "头头低但底底高（收敛/震荡）"
            )

    def _calculate_mas(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算均线"""
        df = df.copy()
        df['MA5'] = df['close'].rolling(window=5).mean()
        df['MA10'] = df['close'].rolling(window=10).mean()
        df['MA20'] = df['close'].rolling(window=20).mean()
        if len(df) >= 60:
            df['MA60'] = df['close'].rolling(window=60).mean()
        else:
            df['MA60'] = df['MA20']  # 数据不足时使用 MA20 替代
        return df

    def _calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算 MACD 指标

        公式：
        - EMA(12)：12日指数移动平均
        - EMA(26)：26日指数移动平均
        - DIF = EMA(12) - EMA(26)
        - DEA = EMA(DIF, 9)
        - MACD = (DIF - DEA) * 2
        """
        df = df.copy()

        # 计算快慢线 EMA
        ema_fast = df['close'].ewm(span=self.MACD_FAST, adjust=False).mean()
        ema_slow = df['close'].ewm(span=self.MACD_SLOW, adjust=False).mean()

        # 计算快线 DIF
        df['MACD_DIF'] = ema_fast - ema_slow

        # 计算信号线 DEA
        df['MACD_DEA'] = df['MACD_DIF'].ewm(span=self.MACD_SIGNAL, adjust=False).mean()

        # 计算柱状图
        df['MACD_BAR'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2

        return df

    def _calculate_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算 RSI 指标（Wilder's EMA / SMMA 口径）

        公式：
        - avg_gain / avg_loss 使用 ewm(alpha=1/period, adjust=False)
        - RS = avg_gain / avg_loss
        - RSI = 100 - (100 / (1 + RS))
        """
        df = df.copy()

        for period in [self.RSI_SHORT, self.RSI_MID, self.RSI_LONG]:
            # 计算价格变化
            delta = df['close'].diff()

            # 分离上涨和下跌
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)

            # 使用 Wilder's EMA / SMMA 口径，与常见 RSI 图表工具保持一致。
            avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

            # 计算 RS 和 RSI
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            # 填充 NaN 值
            rsi = rsi.fillna(50)  # 默认中性值

            # 添加到 DataFrame
            col_name = f'RSI_{period}'
            df[col_name] = rsi

        return df
    
    def _analyze_trend(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """
        分析趋势状态
        
        核心逻辑：判断均线排列和趋势强度
        """
        ma5, ma10, ma20 = result.ma5, result.ma10, result.ma20
        
        # 判断均线排列
        if ma5 > ma10 > ma20:
            # 检查间距是否在扩大（强势）
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev['MA5'] - prev['MA20']) / prev['MA20'] * 100 if prev['MA20'] > 0 else 0
            curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BULL
                result.ma_alignment = "强势多头排列，均线发散上行"
                result.trend_strength = 90
            else:
                result.trend_status = TrendStatus.BULL
                result.ma_alignment = "多头排列 MA5>MA10>MA20"
                result.trend_strength = 75
                
        elif ma5 > ma10 and ma10 <= ma20:
            result.trend_status = TrendStatus.WEAK_BULL
            result.ma_alignment = "弱势多头，MA5>MA10 但 MA10≤MA20"
            result.trend_strength = 55
            
        elif ma5 < ma10 < ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev['MA20'] - prev['MA5']) / prev['MA5'] * 100 if prev['MA5'] > 0 else 0
            curr_spread = (ma20 - ma5) / ma5 * 100 if ma5 > 0 else 0
            
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BEAR
                result.ma_alignment = "强势空头排列，均线发散下行"
                result.trend_strength = 10
            else:
                result.trend_status = TrendStatus.BEAR
                result.ma_alignment = "空头排列 MA5<MA10<MA20"
                result.trend_strength = 25
                
        elif ma5 < ma10 and ma10 >= ma20:
            result.trend_status = TrendStatus.WEAK_BEAR
            result.ma_alignment = "弱势空头，MA5<MA10 但 MA10≥MA20"
            result.trend_strength = 40
            
        else:
            result.trend_status = TrendStatus.CONSOLIDATION
            result.ma_alignment = "均线缠绕，趋势不明"
            result.trend_strength = 50
    
    def _calculate_bias(self, result: TrendAnalysisResult) -> None:
        """
        计算乖离率
        
        乖离率 = (现价 - 均线) / 均线 * 100%
        
        严进策略：乖离率超过 5% 不追高
        """
        price = result.current_price
        
        if result.ma5 > 0:
            result.bias_ma5 = (price - result.ma5) / result.ma5 * 100
        if result.ma10 > 0:
            result.bias_ma10 = (price - result.ma10) / result.ma10 * 100
        if result.ma20 > 0:
            result.bias_ma20 = (price - result.ma20) / result.ma20 * 100
    
    def _analyze_volume(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """
        分析量能
        
        偏好：缩量回调 > 放量上涨 > 缩量上涨 > 放量下跌
        """
        if len(df) < 5:
            return
        
        latest = df.iloc[-1]
        vol_5d_avg = df['volume'].iloc[-6:-1].mean()
        
        if vol_5d_avg > 0:
            result.volume_ratio_5d = float(latest['volume']) / vol_5d_avg
        
        # 判断价格变化
        prev_close = df.iloc[-2]['close']
        price_change = (latest['close'] - prev_close) / prev_close * 100
        
        # 量能状态判断
        if result.volume_ratio_5d >= self.VOLUME_HEAVY_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_UP
                result.volume_trend = "放量上涨，多头力量强劲"
            else:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_DOWN
                result.volume_trend = "放量下跌，注意风险"
        elif result.volume_ratio_5d <= self.VOLUME_SHRINK_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_UP
                result.volume_trend = "缩量上涨，上攻动能不足"
            else:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_DOWN
                result.volume_trend = "缩量回调，洗盘特征明显（好）"
        else:
            result.volume_status = VolumeStatus.NORMAL
            result.volume_trend = "量能正常"
    
    def _analyze_support_resistance(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """
        分析支撑压力位
        
        买点偏好：回踩 MA5/MA10 获得支撑
        """
        price = result.current_price
        
        # 检查是否在 MA5 附近获得支撑
        if result.ma5 > 0:
            ma5_distance = abs(price - result.ma5) / result.ma5
            if ma5_distance <= self.MA_SUPPORT_TOLERANCE and price >= result.ma5:
                result.support_ma5 = True
                result.support_levels.append(result.ma5)
        
        # 检查是否在 MA10 附近获得支撑
        if result.ma10 > 0:
            ma10_distance = abs(price - result.ma10) / result.ma10
            if ma10_distance <= self.MA_SUPPORT_TOLERANCE and price >= result.ma10:
                result.support_ma10 = True
                if result.ma10 not in result.support_levels:
                    result.support_levels.append(result.ma10)
        
        # MA20 作为重要支撑
        if result.ma20 > 0 and price >= result.ma20:
            result.support_levels.append(result.ma20)
        
        # 近期高点作为压力
        if len(df) >= 20:
            recent_high = df['high'].iloc[-20:].max()
            if recent_high > price:
                result.resistance_levels.append(recent_high)

    def _analyze_macd(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """
        分析 MACD 指标

        核心信号：
        - 零轴上金叉：最强买入信号
        - 金叉：DIF 上穿 DEA
        - 死叉：DIF 下穿 DEA
        """
        if len(df) < self.MACD_SLOW:
            result.macd_signal = "数据不足"
            return

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # 获取 MACD 数据
        result.macd_dif = float(latest['MACD_DIF'])
        result.macd_dea = float(latest['MACD_DEA'])
        result.macd_bar = float(latest['MACD_BAR'])

        # 判断金叉死叉
        prev_dif_dea = prev['MACD_DIF'] - prev['MACD_DEA']
        curr_dif_dea = result.macd_dif - result.macd_dea

        # 金叉：DIF 上穿 DEA
        is_golden_cross = prev_dif_dea <= 0 and curr_dif_dea > 0

        # 死叉：DIF 下穿 DEA
        is_death_cross = prev_dif_dea >= 0 and curr_dif_dea < 0

        # 零轴穿越
        prev_zero = prev['MACD_DIF']
        curr_zero = result.macd_dif
        is_crossing_up = prev_zero <= 0 and curr_zero > 0
        is_crossing_down = prev_zero >= 0 and curr_zero < 0

        # 判断 MACD 状态
        if is_golden_cross and curr_zero > 0:
            result.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
            result.macd_signal = "⭐ 零轴上金叉，强烈买入信号！"
        elif is_crossing_up:
            result.macd_status = MACDStatus.CROSSING_UP
            result.macd_signal = "⚡ DIF上穿零轴，趋势转强"
        elif is_golden_cross:
            result.macd_status = MACDStatus.GOLDEN_CROSS
            result.macd_signal = "✅ 金叉，趋势向上"
        elif is_death_cross:
            result.macd_status = MACDStatus.DEATH_CROSS
            result.macd_signal = "❌ 死叉，趋势向下"
        elif is_crossing_down:
            result.macd_status = MACDStatus.CROSSING_DOWN
            result.macd_signal = "⚠️ DIF下穿零轴，趋势转弱"
        elif result.macd_dif > 0 and result.macd_dea > 0:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = "✓ 多头排列，持续上涨"
        elif result.macd_dif < 0 and result.macd_dea < 0:
            result.macd_status = MACDStatus.BEARISH
            result.macd_signal = "⚠ 空头排列，持续下跌"
        else:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = " MACD 中性区域"

    def _analyze_rsi(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """
        分析 RSI 指标

        核心判断：
        - RSI > 70：超买，谨慎追高
        - RSI < 30：超卖，关注反弹
        - 40-60：中性区域
        """
        if len(df) < self.RSI_LONG:
            result.rsi_signal = "数据不足"
            return

        latest = df.iloc[-1]

        # 获取 RSI 数据
        result.rsi_6 = float(latest[f'RSI_{self.RSI_SHORT}'])
        result.rsi_12 = float(latest[f'RSI_{self.RSI_MID}'])
        result.rsi_24 = float(latest[f'RSI_{self.RSI_LONG}'])

        # 以中期 RSI(12) 为主进行判断
        rsi_mid = result.rsi_12

        # 判断 RSI 状态
        if rsi_mid > self.RSI_OVERBOUGHT:
            result.rsi_status = RSIStatus.OVERBOUGHT
            result.rsi_signal = f"⚠️ RSI超买({rsi_mid:.1f}>70)，短期回调风险高"
        elif rsi_mid > 60:
            result.rsi_status = RSIStatus.STRONG_BUY
            result.rsi_signal = f"✅ RSI强势({rsi_mid:.1f})，多头力量充足"
        elif rsi_mid >= 40:
            result.rsi_status = RSIStatus.NEUTRAL
            result.rsi_signal = f" RSI中性({rsi_mid:.1f})，震荡整理中"
        elif rsi_mid >= self.RSI_OVERSOLD:
            result.rsi_status = RSIStatus.WEAK
            result.rsi_signal = f"⚡ RSI弱势({rsi_mid:.1f})，关注反弹"
        else:
            result.rsi_status = RSIStatus.OVERSOLD
            result.rsi_signal = f"⭐ RSI超卖({rsi_mid:.1f}<30)，反弹机会大"

    def _generate_signal(self, result: TrendAnalysisResult) -> None:
        """
        生成买入信号

        综合评分系统：
        - 趋势（30分）：多头排列得分高
        - 乖离率（20分）：接近 MA5 得分高
        - 量能（15分）：缩量回调得分高
        - 支撑（10分）：获得均线支撑得分高
        - MACD（15分）：金叉和多头得分高
        - RSI（10分）：超卖和强势得分高
        """
        score = 0
        reasons = []
        risks = []

        # === 趋势评分（30分）===
        trend_scores = {
            TrendStatus.STRONG_BULL: 30,
            TrendStatus.BULL: 26,
            TrendStatus.WEAK_BULL: 18,
            TrendStatus.CONSOLIDATION: 12,
            TrendStatus.WEAK_BEAR: 8,
            TrendStatus.BEAR: 4,
            TrendStatus.STRONG_BEAR: 0,
        }
        trend_score = trend_scores.get(result.trend_status, 12)
        score += trend_score

        if result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL]:
            reasons.append(f"✅ {result.trend_status.value}，顺势做多")
        elif result.trend_status in [TrendStatus.BEAR, TrendStatus.STRONG_BEAR]:
            risks.append(f"⚠️ {result.trend_status.value}，不宜做多")

        # === 乖离率评分（20分，强势趋势补偿）===
        bias = result.bias_ma5
        if bias != bias or bias is None:  # NaN or None defense
            bias = 0.0
        base_threshold = get_config().bias_threshold

        # Strong trend compensation: relax threshold for STRONG_BULL with high strength
        trend_strength = result.trend_strength if result.trend_strength == result.trend_strength else 0.0
        if result.trend_status == TrendStatus.STRONG_BULL and (trend_strength or 0) >= 70:
            effective_threshold = base_threshold * 1.5
            is_strong_trend = True
        else:
            effective_threshold = base_threshold
            is_strong_trend = False

        if bias < 0:
            # Price below MA5 (pullback)
            if bias > -3:
                score += 20
                reasons.append(f"✅ 价格略低于MA5({bias:.1f}%)，回踩买点")
            elif bias > -5:
                score += 16
                reasons.append(f"✅ 价格回踩MA5({bias:.1f}%)，观察支撑")
            else:
                score += 8
                risks.append(f"⚠️ 乖离率过大({bias:.1f}%)，可能破位")
        elif bias < 2:
            score += 18
            reasons.append(f"✅ 价格贴近MA5({bias:.1f}%)，介入好时机")
        elif bias < base_threshold:
            score += 14
            reasons.append(f"⚡ 价格略高于MA5({bias:.1f}%)，可小仓介入")
        elif bias > effective_threshold:
            score += 4
            risks.append(
                f"❌ 乖离率过高({bias:.1f}%>{effective_threshold:.1f}%)，严禁追高！"
            )
        elif bias > base_threshold and is_strong_trend:
            score += 10
            reasons.append(
                f"⚡ 强势趋势中乖离率偏高({bias:.1f}%)，可轻仓追踪"
            )
        else:
            score += 4
            risks.append(
                f"❌ 乖离率过高({bias:.1f}%>{base_threshold:.1f}%)，严禁追高！"
            )

        # === 量能评分（15分）===
        volume_scores = {
            VolumeStatus.SHRINK_VOLUME_DOWN: 15,  # 缩量回调最佳
            VolumeStatus.HEAVY_VOLUME_UP: 12,     # 放量上涨次之
            VolumeStatus.NORMAL: 10,
            VolumeStatus.SHRINK_VOLUME_UP: 6,     # 无量上涨较差
            VolumeStatus.HEAVY_VOLUME_DOWN: 0,    # 放量下跌最差
        }
        vol_score = volume_scores.get(result.volume_status, 8)
        score += vol_score

        if result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
            reasons.append("✅ 缩量回调，主力洗盘")
        elif result.volume_status == VolumeStatus.HEAVY_VOLUME_DOWN:
            risks.append("⚠️ 放量下跌，注意风险")

        # === 支撑评分（10分）===
        if result.support_ma5:
            score += 5
            reasons.append("✅ MA5支撑有效")
        if result.support_ma10:
            score += 5
            reasons.append("✅ MA10支撑有效")

        # === MACD 评分（15分）===
        macd_scores = {
            MACDStatus.GOLDEN_CROSS_ZERO: 15,  # 零轴上金叉最强
            MACDStatus.GOLDEN_CROSS: 12,      # 金叉
            MACDStatus.CROSSING_UP: 10,       # 上穿零轴
            MACDStatus.BULLISH: 8,            # 多头
            MACDStatus.BEARISH: 2,            # 空头
            MACDStatus.CROSSING_DOWN: 0,       # 下穿零轴
            MACDStatus.DEATH_CROSS: 0,        # 死叉
        }
        macd_score = macd_scores.get(result.macd_status, 5)
        score += macd_score

        if result.macd_status in [MACDStatus.GOLDEN_CROSS_ZERO, MACDStatus.GOLDEN_CROSS]:
            reasons.append(f"✅ {result.macd_signal}")
        elif result.macd_status in [MACDStatus.DEATH_CROSS, MACDStatus.CROSSING_DOWN]:
            risks.append(f"⚠️ {result.macd_signal}")
        else:
            reasons.append(result.macd_signal)

        # === RSI 评分（10分）===
        rsi_scores = {
            RSIStatus.OVERSOLD: 10,       # 超卖最佳
            RSIStatus.STRONG_BUY: 8,     # 强势
            RSIStatus.NEUTRAL: 5,        # 中性
            RSIStatus.WEAK: 3,            # 弱势
            RSIStatus.OVERBOUGHT: 0,       # 超买最差
        }
        rsi_score = rsi_scores.get(result.rsi_status, 5)
        score += rsi_score

        if result.rsi_status in [RSIStatus.OVERSOLD, RSIStatus.STRONG_BUY]:
            reasons.append(f"✅ {result.rsi_signal}")
        elif result.rsi_status == RSIStatus.OVERBOUGHT:
            risks.append(f"⚠️ {result.rsi_signal}")
        else:
            reasons.append(result.rsi_signal)

        # === 综合判断 ===
        result.signal_score = score
        result.signal_reasons = reasons
        result.risk_factors = risks

        # 生成买入信号（调整阈值以适应新的100分制）
        if score >= 75 and result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL]:
            result.buy_signal = BuySignal.STRONG_BUY
        elif score >= 60 and result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL, TrendStatus.WEAK_BULL]:
            result.buy_signal = BuySignal.BUY
        elif score >= 45:
            result.buy_signal = BuySignal.HOLD
        elif score >= 30:
            result.buy_signal = BuySignal.WAIT
        elif result.trend_status in [TrendStatus.BEAR, TrendStatus.STRONG_BEAR]:
            result.buy_signal = BuySignal.STRONG_SELL
        else:
            result.buy_signal = BuySignal.SELL
    
    def format_analysis(self, result: TrendAnalysisResult) -> str:
        """
        格式化分析结果为文本

        Args:
            result: 分析结果

        Returns:
            格式化的分析文本
        """
        lines = [
            f"=== {result.code} 趋势分析 ===",
            f"",
            f"📊 趋势判断: {result.trend_status.value}",
            f"   均线排列: {result.ma_alignment}",
            f"   趋势强度: {result.trend_strength}/100",
            f"",
            f"📈 均线数据:",
            f"   现价: {result.current_price:.2f}",
            f"   MA5:  {result.ma5:.2f} (乖离 {result.bias_ma5:+.2f}%)",
            f"   MA10: {result.ma10:.2f} (乖离 {result.bias_ma10:+.2f}%)",
            f"   MA20: {result.ma20:.2f} (乖离 {result.bias_ma20:+.2f}%)",
            f"",
            f"📊 量能分析: {result.volume_status.value}",
            f"   量比(vs5日): {result.volume_ratio_5d:.2f}",
            f"   量能趋势: {result.volume_trend}",
            f"",
            f"📈 MACD指标: {result.macd_status.value}",
            f"   DIF: {result.macd_dif:.4f}",
            f"   DEA: {result.macd_dea:.4f}",
            f"   MACD: {result.macd_bar:.4f}",
            f"   信号: {result.macd_signal}",
            f"",
            f"📊 RSI指标: {result.rsi_status.value}",
            f"   RSI(6): {result.rsi_6:.1f}",
            f"   RSI(12): {result.rsi_12:.1f}",
            f"   RSI(24): {result.rsi_24:.1f}",
            f"   信号: {result.rsi_signal}",
            f"",
            f"🎯 操作建议: {result.buy_signal.value}",
            f"   综合评分: {result.signal_score}/100",
        ]

        if result.signal_reasons:
            lines.append(f"")
            lines.append(f"✅ 买入理由:")
            for reason in result.signal_reasons:
                lines.append(f"   {reason}")

        if result.risk_factors:
            lines.append(f"")
            lines.append(f"⚠️ 风险因素:")
            for risk in result.risk_factors:
                lines.append(f"   {risk}")

        return "\n".join(lines)


def analyze_stock(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    """
    便捷函数：分析单只股票
    
    Args:
        df: 包含 OHLCV 数据的 DataFrame
        code: 股票代码
        
    Returns:
        TrendAnalysisResult 分析结果
    """
    analyzer = StockTrendAnalyzer()
    return analyzer.analyze(df, code)


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    # 模拟数据测试
    import numpy as np
    
    dates = pd.date_range(start='2025-01-01', periods=60, freq='D')
    np.random.seed(42)
    
    # 模拟多头排列的数据
    base_price = 10.0
    prices = [base_price]
    for i in range(59):
        change = np.random.randn() * 0.02 + 0.003  # 轻微上涨趋势
        prices.append(prices[-1] * (1 + change))
    
    df = pd.DataFrame({
        'date': dates,
        'open': prices,
        'high': [p * (1 + np.random.uniform(0, 0.02)) for p in prices],
        'low': [p * (1 - np.random.uniform(0, 0.02)) for p in prices],
        'close': prices,
        'volume': [np.random.randint(1000000, 5000000) for _ in prices],
    })
    
    analyzer = StockTrendAnalyzer()
    result = analyzer.analyze(df, '000001')
    print(analyzer.format_analysis(result))

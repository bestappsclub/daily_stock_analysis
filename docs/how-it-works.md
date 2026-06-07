# 系统是如何工作的（源码走读）

> 本文是一份**面向学习**的源码走读，目标是让你顺着真实的调用链，搞清楚「一条股票分析」从命令行到推送通知，中间到底发生了什么。
> 所有代码位置都标了 `文件:行号`，建议打开对应文件边读边对照。
>
> 阅读顺序建议：先跑一遍 `python main.py --dry-run` 和 `python main.py --stocks 600519 --debug`，看着日志读本文，效果最好。

## 0. 一句话总览

整个系统是一条流水线：

```
抓数据  →  技术分析 + 新闻检索 + 基本面  →  喂给 LLM 分析  →  生成报告  →  多渠道推送
```

承载这条流水线的核心类是 `StockAnalysisPipeline`，定义在 [src/core/pipeline.py](src/core/pipeline.py)。理解了它，就理解了整个系统的主干。

## 1. 入口：main.py 做了什么

[main.py](main.py) 是 CLI 入口，职责很克制：

1. **加载配置** —— 启动时就调用 `setup_env()` 读取 `.env`（[main.py:34](main.py#L34)），并按需配置本地代理。
2. **解析命令行参数** —— `--dry-run` / `--stocks` / `--market-review` / `--schedule` / `--serve` / `--webui` 等，决定走哪条路径。
3. **线程池调度** —— 多只股票并发分析（并发数来自配置 `max_workers`）。
4. **全局异常兜底** —— 单只股票失败不会拖垮整批，这是本项目反复强调的设计原则（fallback 优先于 fail-fast）。

最常见的路径是「批量分析自选股」，它会构造一个 `StockAnalysisPipeline` 并调用 `run()`。

## 2. 流水线的心脏：StockAnalysisPipeline

### 2.1 初始化时装配了哪些能力

构造函数 [pipeline.py:95](src/core/pipeline.py#L95) 把所有依赖一次性装好：

| 组件 | 变量 | 作用 |
|---|---|---|
| 数据库 | `self.db` | 本地行情/分析结果落库（断点续传、复盘都靠它） |
| 数据源管理器 | `self.fetcher_manager` (`DataFetcherManager`) | 多数据源聚合 + 自动故障切换 |
| 技术分析器 | `self.trend_analyzer` (`StockTrendAnalyzer`) | 均线 / 趋势 / 量价指标 |
| LLM 分析器 | `self.analyzer` (`GeminiAnalyzer`) | 调用大模型生成分析 |
| 通知服务 | `self.notifier` (`NotificationService`) | 多渠道推送 |
| 搜索服务 | `self.search_service` | 新闻 / 舆情 / 业绩预期检索（**可选**） |
| 社交舆情 | `self.social_sentiment_service` | Reddit/X/Polymarket，仅美股（**可选**） |

注意两个「可选」服务的初始化都包在 `try/except` 里（[pipeline.py:139](src/core/pipeline.py#L139)、[pipeline.py:176](src/core/pipeline.py#L176)）：**初始化失败只降级、不阻断主流程**。这是贯穿全项目的健壮性写法。

### 2.2 run()：批量调度主循环

入口在 [pipeline.py:2260](src/core/pipeline.py#L2260)：

1. 取自选股列表（未传入时用配置里的 `STOCK_LIST`）。
2. **冻结一个统一的参考时间** —— 避免同一批股票因跨市场收盘边界，用到不同的「目标交易日」。
3. 用线程池并发对每只股票调用 `process_single_stock()`（[pipeline.py:2163](src/core/pipeline.py#L2163)）。
4. 收集结果。
5. 统一发送通知 `_send_notifications()`（[pipeline.py:2535](src/core/pipeline.py#L2535)）。

`process_single_stock()` 内部分两步：先 `fetch_and_save_stock_data()` 抓数据落库，再 `analyze_stock()` 做分析。

## 3. 数据获取与落库（含断点续传）

`fetch_and_save_stock_data()`（[pipeline.py:213](src/core/pipeline.py#L213)）的关键设计是**断点续传**：

1. 先算出「本轮应该用哪个交易日」（`_resolve_resume_target_date`）。
2. 如果数据库里**已有该交易日数据**且非强制刷新 → 直接跳过网络请求（[pipeline.py:248](src/core/pipeline.py#L248)）。
3. 否则通过 `fetcher_manager.get_daily_data()` 拉数据，落库。

这意味着任务中断后重跑，不会重复抓已有数据 —— 对每天定时跑、偶尔失败重试的场景很关键。

> 多数据源的优先级与降级逻辑在 [data_provider/](data_provider/)：先看抽象基类 [data_provider/base.py](data_provider/base.py)，再看 akshare / tushare / yfinance 等各家 fetcher。单一数据源失败会自动切到下一家，而不是直接报错。

## 4. analyze_stock()：单只股票的完整分析链

这是最值得精读的方法，[pipeline.py:269](src/core/pipeline.py#L269)。它按步骤拼装「喂给 LLM 的上下文」，每一步都做了异常降级：

| 步骤 | 代码位置 | 做什么 | 失败时 |
|---|---|---|---|
| 市场阶段上下文 | [:303](src/core/pipeline.py#L303) | 判断现在处于开盘前/盘中/收盘后等阶段 | — |
| Step 1 实时行情 | [:319](src/core/pipeline.py#L319) | 量比、换手率、最新价、真实股票名 | 降级为历史收盘价 |
| Step 2 筹码分布 | [:344](src/core/pipeline.py#L344) | 获利比例、集中度 | 跳过 |
| 判断是否走 Agent | [:358](src/core/pipeline.py#L358) | 显式开启 `agent_mode` 或配置了策略 skill 才切 Agent | 默认走传统路径 |
| Step 2.5 基本面聚合 | [:380](src/core/pipeline.py#L380) | 带超时预算地聚合基本面 | 返回 partial/failed，不阻断 |
| Step 3 趋势分析 | [:417](src/core/pipeline.py#L417) | 从库里取 ~60 交易日数据算均线/趋势/评分 | 跳过 |
| Step 4 多维情报搜索 | [:448](src/core/pipeline.py#L448) | 最新消息 + 风险排查 + 业绩预期（最多 5 次搜索） | 搜索不可用则跳过 |
| Step 4.5 社交舆情 | [:486](src/core/pipeline.py#L486) | 仅美股 | 跳过 |
| Step 5 技术面上下文 | [:500](src/core/pipeline.py#L500) | 从数据库取分析上下文 | 标记 `data_missing` 仍继续 |
| Step 6 增强上下文 | [:516](src/core/pipeline.py#L516) | `_enhance_context()` 把实时/筹码/趋势/基本面/市场阶段合并 | — |
| Step 7 调用 LLM | [:531](src/core/pipeline.py#L531) 起 | 构建「分析上下文包」并流式调用大模型 | — |

### 4.1 关键分叉：传统路径 vs Agent 路径

在 [pipeline.py:358](src/core/pipeline.py#L358) 有个重要判断 `use_agent`：

- **默认走传统路径**：一次性把上下文喂给 LLM，产出结构化分析结果。
- **只有显式开启 `agent_mode`，或请求/配置了具体策略 skill 时才走 Agent**（`_analyze_with_agent`，[pipeline.py:968](src/core/pipeline.py#L968)）。

设计上特意没用「只要有 API Key 就启用 Agent」，因为 Agent 更慢更贵 —— 见 [pipeline.py:351](src/core/pipeline.py#L351) 的注释。这是「不给用户制造意外开销」的体贴设计。

### 4.2 喂给 LLM 的不是裸数据，而是「上下文包」

LLM 拿到的是经过组织的 `AnalysisContextPack`（[pipeline.py:533](src/core/pipeline.py#L533) `_build_analysis_context_pack_outputs`）。这一层把技术面、基本面、新闻、市场阶段统一成结构化、有质量标记的上下文。
想深入理解分析质量是怎么来的，读 [docs/analysis-context-pack.md](docs/analysis-context-pack.md)。

### 4.3 流式进度

注意 `_emit_progress()`（[pipeline.py:191](src/core/pipeline.py#L191)）贯穿全程：18% 取行情 → 32% 聚合基本面 → 46% 检索新闻 → 58% 整理上下文 → 64%+ LLM 流式输出。Web/桌面端的进度条就是靠它驱动的。

## 5. 交易理念是怎么编码进系统的

打开 [main.py](main.py) 顶部注释你会看到，作者把一套交易纪律直接写进了系统：

- 严进策略：不追高，乖离率 > 5% 不买入
- 趋势交易：只做 MA5 > MA10 > MA20 多头排列
- 效率优先：关注筹码集中度
- 买点偏好：缩量回踩 MA5/MA10 支撑

这些规则一部分体现在 `StockTrendAnalyzer` 的打分里（趋势状态、买入信号、`signal_score`，见 [pipeline.py:431](src/core/pipeline.py#L431)），一部分写进了 LLM 的 prompt。**这是本项目的灵魂：它不是「让 AI 随便聊聊」，而是把人的交易框架结构化后交给 AI 执行。**

## 6. 报告生成与推送

分析结果（`AnalysisResult`）回到 `run()` 后：

1. `_generate_aggregate_report()`（[pipeline.py:3062](src/core/pipeline.py#L3062)）汇总成「决策仪表盘」。
2. `_send_notifications()`（[pipeline.py:2535](src/core/pipeline.py#L2535)）分发到各渠道。

通知层做了很干净的「契约 / 路由 / 能力 / 发送」分层，值得单独学：

| 文件 | 职责 |
|---|---|
| [src/notification_contracts.py](src/notification_contracts.py) | 通知的数据契约 |
| [src/notification_routing.py](src/notification_routing.py) | 该发给谁、走哪个渠道 |
| [src/notification_capabilities.py](src/notification_capabilities.py) | 各渠道支持什么（如是否支持图片） |
| [src/notification_sender/](src/notification_sender/) | 各渠道的具体发送实现 |

同样遵循「单一渠道失败不拖垮主流程」原则。

## 7. 主流程之外的几条支线

- **大盘复盘**：`--market-review` → [src/core/market_review.py](src/core/market_review.py) / [src/market_analyzer.py](src/market_analyzer.py)
- **Agent 问股**：[src/agent/](src/agent/)，多轮对话 + 工具调用 + 15 种内置策略，入口看 `orchestrator` / `executor` / `strategies` / `tools`
- **回测**：[src/core/backtest_engine.py](src/core/backtest_engine.py)
- **Web / API 服务**：`--serve` → [server.py](server.py) + [api/](api/)，前端在 [apps/dsa-web/](apps/dsa-web/)
- **桌面端**：[apps/dsa-desktop/](apps/dsa-desktop/)（Electron）
- **定时与交易日历**：`--schedule` → [src/scheduler.py](src/scheduler.py) + [src/core/trading_calendar.py](src/core/trading_calendar.py)（非交易日默认不跑）

## 8. 三条贯穿全项目的设计原则（最值得带走的）

1. **fallback 优先于 fail-fast**：任何单一数据源 / 搜索源 / 通知渠道失败，都降级而不是中断主流程。
2. **配置「不配也能跑，配了增强」**：搜索、社交舆情、基本面、Agent 全是可选增强，缺了照样产出报告。
3. **向后兼容优先**：API / Schema / 报告字段改动优先追加、保留旧字段，照顾 Web 和桌面端多客户端。

这三点在 [CLAUDE.md](CLAUDE.md)（即 `AGENTS.md`）里被列为硬规则，也是读这份代码时反复能验证到的工程品味。

## 9. 建议的动手实验

```bash
# 1. 只抓数据不分析，理解数据层
python main.py --dry-run

# 2. 完整分析单只股票 + 详细日志，对照本文第 4 节逐步看
python main.py --stocks 600519 --debug
python main.py --stocks BS6.SI --debug      # 新加坡个股（注意 .SI 后缀）

# 3. 跑大盘复盘（可指定市场）
python main.py --market-review
MARKET_REVIEW_REGION=us python main.py --market-review

# 4. 起 Web 工作台，从 UI 反向理解 API
python main.py --webui   # 然后访问 http://127.0.0.1:8000

# 5. 灌本地行情缓存后，体验秒级全市场选股（见第 11 节）
python scripts/sync_prices.py --markets us --days 150
```

边看日志边对照 `analyze_stock()` 的各个 Step，是理解这套系统最快的方式。

---

# 进阶能力（本轮扩展，单独成节便于学习）

## 10. 原生多市场选股（美股 / 新加坡 / DK / 摆动结构）

**美股(us)/新加坡(sg) 是 DSA 自建的原生选股器**；**A股(cn) 可选原生**（`CN_SCREEN_NATIVE=true` 开启，默认关时仍走外部 `alphasift` 包）。各市场通过同一个 `/api/v1/alphasift/screen` 按 `market` 分发，复用同一个 Web「选股」页。A股原生数据走 akshare 前复权（`data_provider/akshare_fetcher.py:batch_download_cn_daily`，东方财富→新浪→腾讯 fallback），默认池 `src/data/cn_universe.txt`（全 A股+北交所 ~5500），由 `scripts/fetch_cn_universe.py` 从前端股票索引生成。

- 核心：[src/services/us_screener_service.py](src/services/us_screener_service.py) 的 `MarketScreenerService` —— 扫描**有界股票池**，对每只调用与个股分析相同的 `StockTrendAnalyzer` 打分，按策略排序，可选 LLM 重排。
- 股票池（静态可提交文件，确定性、不依赖运行时网络）：
  - 美股 [src/data/us_universe.txt](src/data/us_universe.txt)（标普 Composite 1500）
  - 新加坡 [src/data/sg_universe.txt](src/data/sg_universe.txt)（SGX 全主板 ~615，由 [scripts/fetch_sg_universe.py](scripts/fetch_sg_universe.py) 从 SGX 官方列表生成）
- 内置策略（id 形如 `us_*` / `sg_*`）：趋势动量 / 放量突破 / 超跌反转 / 多头趋势 / **多头结构 / 空头结构** / **DK买点**。
- API 分发：[api/v1/endpoints/alphasift.py](api/v1/endpoints/alphasift.py) `_native_screen_market()`。
- 详见 [docs/us-screening.md](us-screening.md)。

两个特色指标都在 [src/stock_analyzer.py](src/stock_analyzer.py) 计算，作为字段挂在 `TrendAnalysisResult` 上：

- **摆动结构（道氏）**：`_analyze_swing_structure()` 用 fractal 找摆动高/低点，头头高+底底高=`structure=bull`，头头低+底底低=`bear`。
- **东财式 DK 买卖点**：`_analyze_dk()` 价格突破 N 日高(放量折扣)转持股(D点/买)、跌破 N 日低转持币(K点/卖)，输出 `dk_state`(hold/cash)/`dk_signal`(D/K)。算法与 stockscreener `_dk_buysell_state` 一致，完整说明见 stockscreener 项目 `docs/dk-indicator.md`。要与东财对齐需前复权数据。

## 11. 本地行情缓存（加速 / 离线 / 不限流）

选股默认**优先读本地 `stock_daily` 表缓存**，只对缺失/过期标的 live 补抓并回写：

- 灌库 / 增量刷新：[scripts/sync_prices.py](scripts/sync_prices.py)（`--markets us,sg --days 150`，已新鲜的跳过）；或 Web「选股」页的**「同步行情缓存」按钮** → `POST /api/v1/alphasift/sync-cache`。三者同一份逻辑 `MarketScreenerService.sync_cache()`。
- 缓存读写：`MarketScreenerService._load_frames()` + 仓储层 [src/repositories/stock_repo.py](src/repositories/stock_repo.py)（`get_range`/`save_dataframe`），底层 [src/storage.py](src/storage.py) 的 `StockDaily` 表与 `save_daily_data()` upsert。
- 开关：`<PREFIX>_USE_CACHE`（默认开）、`SCREEN_CACHE_STALE_DAYS`（默认 2 天）；**缓存层任何异常自动回退 live**。
- 数据库 `data/stock_analysis.db` 已 gitignore，不入库（约 150 字节/行，us+sg≈2100 只×150 天≈47MB）。
- 体感：全缓存重复扫描毫秒级（实测 5 只 ~200ms）。

## 12. 大盘复盘选市场 + 新加坡个股输入

- **复盘选市场**：Web 首页「大盘复盘」按钮旁的市场下拉（A股/港股/美股/新加坡/全部）→ `MarketReviewRequest.region` → [api/v1/endpoints/analysis.py](api/v1/endpoints/analysis.py) `trigger_market_review`（显式 region 跳过交易日过滤）。`cn/hk/us/sg/both` 由 [src/core/trading_calendar.py](src/core/trading_calendar.py) `compute_effective_region` 解析；复盘执行 [src/core/market_review.py](src/core/market_review.py)。缺省仍按 `MARKET_REVIEW_REGION` 配置 + 当日交易日自动判定。
- **SG 个股输入**：自动补全索引 [apps/dsa-web/public/stocks.index.json](apps/dsa-web/public/stocks.index.json) 纳入 SGX 全主板（由 [scripts/add_sg_to_stock_index.py](scripts/add_sg_to_stock_index.py) 注入）；输入裸代码/名称（`BS6`/`DBS`）经 `resolveQueryToCanonicalCode` 解析成 `.SI` 规范代码；前端 [validation.ts](apps/dsa-web/src/utils/validation.ts) 与后端 [stock_code_utils.py](src/services/stock_code_utils.py) 都放行 `.SI` 格式；市场识别 `.SI → sg`（时区 Asia/Singapore，日历 XSES）。

## 13. 本地常驻与定时（macOS）

- **Web 服务常驻**：`com.dsa.webserver`（登录自启 + 保活，端口 8000）。
- **行情缓存每日刷新**：`com.dsa.pricesync`（每天 06:00/18:00 跑 `sync_prices.py`，增量）。
- 配置与管理命令见 [docs/run-local-service.md](run-local-service.md)。

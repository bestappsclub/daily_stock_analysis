# 美股 / 新加坡选股（多市场原生选股）

DSA 内置一个**原生多市场选股器**，覆盖 **美股（us）** 与 **新加坡（sg）**，与 A 股 AlphaSift 选股相互独立。它扫描一个**有界股票池**，用与个股分析相同的趋势引擎给每只股票打分、按策略排序，可选地用 LLM 重排出推荐理由。

| 市场 | 默认股票池 | 代码风格 |
| --- | --- | --- |
| 美股 `us` | 标普 Composite 1500（500+400+600，约 1500 只） | 裸代码，如 `AAPL`、`BRK-B` |
| 新加坡 `sg` | 海峡时报指数 STI 成分股（约 30 只） | yfinance 风格 `.SI` 后缀，如 `D05.SI` |

> 与 AlphaSift 的关系：A 股选股由外部 `alphasift` 包提供（策略 `market_scope=['cn']`）；美股/新加坡选股是 DSA 原生能力，三者通过同一个 `/api/v1/alphasift/screen` 接口按 `market` 字段分发，复用同一个 Web「选股」页。

## 为什么是「有界股票池」

免费数据源（yfinance）无法高效扫描全部 8000+ 只美股（速率限制 + 耗时）。因此默认池为**标普 Composite 1500**（标普 500 + 400 中盘 + 600 小盘，约 1500 只流动性较好的标的），可通过配置自定义或扩大/缩小。

## 使用

1. 启动 Web 服务（`python main.py --serve-only` 或 `./run-local.sh`）。
2. 打开「选股」页，**市场选「美股」或「新加坡」**。
3. 选择策略 → 运行 → 查看候选股、评分、推荐理由。

> 资源提示：全市场扫描较重，建议在**本地或 ≥2GB 内存的服务器**运行；Render 免费档（512MB）容易因内存不足在扫描中被杀重启。

## 内置策略

| 策略 ID | 名称 | 选股逻辑（基于趋势引擎输出） |
| --- | --- | --- |
| `us_momentum` | 趋势动量 | 多头趋势中按综合评分 + 趋势强度排序 |
| `us_breakout` | 放量突破 | 放量上涨且出现买入信号 |
| `us_oversold` | 超跌反转 | RSI 超卖、具备反转潜力 |
| `us_trend_quality` | 多头趋势 | MA5>MA10>MA20 多头排列、趋势质量高 |
| `us_structure_bull` | 多头结构 | 道氏摆动结构：**头头高 + 底底高** |
| `us_structure_bear` | 空头结构 | 道氏摆动结构：**头头低 + 底底低**（最弱在前） |

新加坡策略 id 同形，前缀换为 `sg_`（如 `sg_structure_bull`）。命中为空时自动降级为对全集排序，避免返回空结果。

> 摆动结构（道氏理论）：用 fractal 法找摆动高/低点（某根比左右各 N 根都高/低；N 默认 3，经 `SWING_PIVOT_WINDOW` 覆盖），比较最近两个高点与两个低点：头头高+底底高=多头、头头低+底底低=空头。该结构同时作为 `structure` 字段出现在个股趋势分析结果中。

## 配置（环境变量，见 `.env.example`）

每个市场一套同形配置，前缀分别为 `US_SCREEN` / `SG_SCREEN`（下表以 `<PREFIX>` 表示）：

| 变量 | 美股默认 | 新加坡默认 | 说明 |
| --- | --- | --- | --- |
| `<PREFIX>_ENABLED` | `true` | `true` | 总开关（原生能力，无需安装） |
| `<PREFIX>_UNIVERSE` | 空 | 空 | 逗号分隔代码，覆盖默认池（sg 需带 `.SI`） |
| `<PREFIX>_UNIVERSE_FILE` | `src/data/us_universe.txt` | `src/data/sg_universe.txt` | 默认股票池文件 |
| `<PREFIX>_MAX_UNIVERSE` | `1500` | `200` | 单次扫描标的上限 |
| `<PREFIX>_HISTORY_DAYS` | `150` | `150` | 回看自然日（约 100 交易日，够算 MA60） |
| `<PREFIX>_LLM_RERANK` | `true` | `true` | 对 Top 候选做 LLM 重排；失败自动降级为因子排序 |
| `<PREFIX>_LLM_RERANK_TOP` | `15` | `15` | 参与 LLM 重排的候选数 |
| `<PREFIX>_ENRICH` | `false` | `false` | 给候选补行情/基本面/新闻（较慢）；美股建议配 Brave/SerpAPI/Tavily 新闻源 |

## 新加坡（SGX）补充

- 选股之外，新加坡也支持**个股深度分析**（如 `python main.py --stocks D05.SI`）与**大盘复盘**（`MARKET_REVIEW_REGION=sg` 或 `both`，复盘海峡时报指数 STI）。
- SG 代码统一带 `.SI` 后缀（yfinance 原生），系统据此识别为 `sg` 市场（时区 `Asia/Singapore`，交易日历 `XSES`）。
- 数据仅由 yfinance 提供（akshare/tushare 等不覆盖 SGX）。

## 数据与降级语义

- **行情**：用 yfinance 批量下载日线；单只失败/无数据自动跳过并在 `warnings` 记录（fail-open），不中断整体。
- **LLM 重排**：未配置或调用失败时降级为纯因子排序，候选仍带技术面推荐理由。
- **DSA 增强**：默认关闭；开启后失败不影响候选输出。

## 局限

- 仅覆盖默认/自定义的有界股票池，非全量市场（美股约 1500、新加坡约 30）。
- 因子与评分复用统一的趋势引擎（均线/量价/MACD/RSI），未做各市场专属因子定制。
- 港股暂不支持选股（仅支持个股分析 / 大盘复盘的既有范围）。

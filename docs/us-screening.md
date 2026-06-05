# 美股选股（US Screening）

DSA 内置一个**原生美股选股器**，与 A 股 AlphaSift 选股相互独立。它扫描一个**有界股票池**（默认标普 Composite 1500），用与个股分析相同的趋势引擎给每只股票打分、按策略排序，可选地用 LLM 重排出推荐理由。

> 与 AlphaSift 的关系：A 股选股由外部 `alphasift` 包提供（策略 `market_scope=['cn']`）；美股选股是 DSA 原生能力，二者通过同一个 `/api/v1/alphasift/screen` 接口按 `market` 字段分发，复用同一个 Web「选股」页。

## 为什么是「有界股票池」

免费数据源（yfinance）无法高效扫描全部 8000+ 只美股（速率限制 + 耗时）。因此默认池为**标普 Composite 1500**（标普 500 + 400 中盘 + 600 小盘，约 1500 只流动性较好的标的），可通过配置自定义或扩大/缩小。

## 使用

1. 启动 Web 服务（`python main.py --serve-only` 或 `./run-local.sh`）。
2. 打开「选股」页，**市场选「美股」**。
3. 选择策略 → 运行 → 查看候选股、评分、推荐理由。

> 资源提示：全市场扫描较重，建议在**本地或 ≥2GB 内存的服务器**运行；Render 免费档（512MB）容易因内存不足在扫描中被杀重启。

## 内置策略

| 策略 ID | 名称 | 选股逻辑（基于趋势引擎输出） |
| --- | --- | --- |
| `us_momentum` | 趋势动量 | 多头趋势中按综合评分 + 趋势强度排序 |
| `us_breakout` | 放量突破 | 放量上涨且出现买入信号 |
| `us_oversold` | 超跌反转 | RSI 超卖、具备反转潜力 |
| `us_trend_quality` | 多头趋势 | MA5>MA10>MA20 多头排列、趋势质量高 |

命中为空时自动降级为对全集排序，避免返回空结果。

## 配置（环境变量，见 `.env.example`）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `US_SCREEN_ENABLED` | `true` | 总开关（原生能力，无需安装） |
| `US_SCREEN_UNIVERSE` | 空 | 逗号分隔代码，覆盖默认池（如 `AAPL,MSFT,NVDA`） |
| `US_SCREEN_UNIVERSE_FILE` | `src/data/us_universe.txt` | 默认股票池文件（yfinance 风格代码，如 `BRK-B`） |
| `US_SCREEN_MAX_UNIVERSE` | `1500` | 单次扫描标的上限 |
| `US_SCREEN_HISTORY_DAYS` | `150` | 回看自然日（约 100 交易日，够算 MA60） |
| `US_SCREEN_LLM_RERANK` | `true` | 对 Top 候选做 LLM 重排，产出推荐理由/催化/风险；失败自动降级为因子排序 |
| `US_SCREEN_LLM_RERANK_TOP` | `15` | 参与 LLM 重排的候选数 |
| `US_SCREEN_ENRICH` | `false` | 给候选补行情/基本面/新闻（较慢）；开启建议配 Brave/SerpAPI/Tavily 美股新闻源 |

## 数据与降级语义

- **行情**：用 yfinance 批量下载日线；单只失败/无数据自动跳过并在 `warnings` 记录（fail-open），不中断整体。
- **LLM 重排**：未配置或调用失败时降级为纯因子排序，候选仍带技术面推荐理由。
- **DSA 增强**：默认关闭；开启后失败不影响候选输出。

## 局限

- 仅覆盖默认/自定义的有界股票池，非全量美股。
- 因子与评分复用 A 股交易理念的趋势引擎（均线/量价/MACD/RSI），未做美股专属因子定制。
- 港股、新加坡等其他市场暂不支持选股（仅支持个股分析 / 大盘复盘的既有范围）。

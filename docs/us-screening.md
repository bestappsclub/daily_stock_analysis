# 美股 / 新加坡选股（多市场原生选股）

DSA 内置一个**原生多市场选股器**，覆盖 **美股（us）** 与 **新加坡（sg）**，与 A 股 AlphaSift 选股相互独立。它扫描一个**有界股票池**，用与个股分析相同的趋势引擎给每只股票打分、按策略排序，可选地用 LLM 重排出推荐理由。

| 市场 | 默认股票池 | 代码风格 |
| --- | --- | --- |
| 美股 `us` | 标普 Composite 1500（500+400+600，约 1500 只） | 裸代码，如 `AAPL`、`BRK-B` |
| 新加坡 `sg` | SGX 全主板：普通股 + REITs + 商业信托（约 615 只） | yfinance 风格 `.SI` 后缀，如 `D05.SI` |

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
| `us_dk_buy` | DK买点 | 东财式 DK 买卖点：当前**持股态**，**刚出 D 点**（价格突破+放量）优先 |

新加坡策略 id 同形，前缀换为 `sg_`（如 `sg_structure_bull`、`sg_dk_buy`）。命中为空时自动降级为对全集排序，避免返回空结果。

> 摆动结构（道氏理论）：用 fractal 法找摆动高/低点（某根比左右各 N 根都高/低；N 默认 3，经 `SWING_PIVOT_WINDOW` 覆盖），比较最近两个高点与两个低点：头头高+底底高=多头、头头低+底底低=空头。该结构同时作为 `structure` 字段出现在个股趋势分析结果中。

> DK 买卖点（东财式）：价格突破 + 放量折扣的状态机——收盘上破 N 日最高（或贴近且放量）转**持股(D点/买)**，跌破 N 日最低转**持币(K点/卖)**；中间形成滞后带，信号稀疏。参数 `DK_NUP=20 / DK_NDN=10 / DK_VASSIST=0.96 / DK_VWIN=20`（可经环境变量覆盖），与 stockscreener 项目 `technical.py:_dk_buysell_state` 一致；个股结果含 `dk_state`(hold/cash) 与 `dk_signal`(D/K) 字段。需前复权数据对齐东财（yfinance 抓取默认前复权）。完整算法见 stockscreener 项目 `docs/dk-indicator.md`。

## 配置（环境变量，见 `.env.example`）

每个市场一套同形配置，前缀分别为 `US_SCREEN` / `SG_SCREEN`（下表以 `<PREFIX>` 表示）：

| 变量 | 美股默认 | 新加坡默认 | 说明 |
| --- | --- | --- | --- |
| `<PREFIX>_ENABLED` | `true` | `true` | 总开关（原生能力，无需安装） |
| `<PREFIX>_UNIVERSE` | 空 | 空 | 逗号分隔代码，覆盖默认池（sg 需带 `.SI`） |
| `<PREFIX>_UNIVERSE_FILE` | `src/data/us_universe.txt` | `src/data/sg_universe.txt` | 默认股票池文件 |
| `<PREFIX>_MAX_UNIVERSE` | `1500` | `700` | 单次扫描标的上限 |
| `<PREFIX>_HISTORY_DAYS` | `150` | `150` | 回看自然日（约 100 交易日，够算 MA60） |
| `<PREFIX>_LLM_RERANK` | `true` | `true` | 对 Top 候选做 LLM 重排；失败自动降级为因子排序 |
| `<PREFIX>_LLM_RERANK_TOP` | `15` | `15` | 参与 LLM 重排的候选数 |
| `<PREFIX>_ENRICH` | `false` | `false` | 给候选补行情/基本面/新闻（较慢）；美股建议配 Brave/SerpAPI/Tavily 新闻源 |

## 新加坡（SGX）补充

- **默认股票池为 SGX 全主板**（普通股 + REITs + 商业信托，约 615 只），由 `scripts/fetch_sg_universe.py` 从 SGX 官方证券列表接口生成、写入 `src/data/sg_universe.txt`（与美股一样是静态可提交文件，确定性、运行时不依赖网络）。需要更新（新上市/退市）时重跑该脚本：

  ```bash
  python scripts/fetch_sg_universe.py            # 重建股票池
  python scripts/fetch_sg_universe.py --dry-run  # 只看统计
  ```

  排除窝轮、结构性权证、DLC、ETF、债券、ADR 等非主板个股标的。**全主板含大量微型股/仙股**，动量类策略可能把低价高波动标的排在前面；只想扫蓝筹时用 `SG_SCREEN_UNIVERSE` 填 STI 成分股或自定义子集。
- 选股之外，新加坡也支持**个股深度分析**（如 `python main.py --stocks D05.SI`）与**大盘复盘**（`MARKET_REVIEW_REGION=sg` 或 `both`，复盘海峡时报指数 STI）。
- SG 代码统一带 `.SI` 后缀（yfinance 原生），系统据此识别为 `sg` 市场（时区 `Asia/Singapore`，交易日历 `XSES`）。**个股分析也必须带 `.SI`**（如 `BS6.SI`），裸代码 `BS6` 不会被识别为新加坡（避免与美股代码冲突）。
- 数据仅由 yfinance 提供（akshare/tushare 等不覆盖 SGX）。

## 本地缓存（加速 + 离线 + 不限流）

选股默认**优先读本地 `stock_daily` 缓存**，只对缺失/过期标的 live 补抓并回写（`<PREFIX>_USE_CACHE=true`，默认开；设 `false` 退回纯 live）。

先灌库（数据库为本地 SQLite `data/stock_analysis.db`，**已 gitignore，不要提交**）：

```bash
python scripts/sync_prices.py                 # 同步 us+sg，默认 150 天，增量
python scripts/sync_prices.py --markets us    # 只美股
python scripts/sync_prices.py --days 500      # 抓约 2 年
python scripts/sync_prices.py --full          # 忽略新鲜度全部重抓
```

也可在 **Web「选股」页**点 **「同步行情缓存」按钮**(选「美股」或「新加坡」时显示)直接触发当前市场的增量同步，无需命令行；对应接口 `POST /api/v1/alphasift/sync-cache`（`{market, full}`，仅 us/sg，A股返回 400）。

- 增量：本地最新日期在 `SCREEN_CACHE_STALE_DAYS`（默认 2）天内则跳过。
- 灌库后重复扫描**秒级/毫秒级**（实测 5 只全缓存约 200ms）。
- CLI、Web 按钮、每日定时任务三者**同一份逻辑**（`MarketScreenerService.sync_cache`）。
- 体量参考：约 150 字节/行；us+sg≈2100 只 × 150 天 ≈ 47MB，2 年 ≈ 158MB。
- 想每日自动刷新：用 macOS LaunchAgent 定时跑本脚本（见 `docs/run-local-service.md` 同类配置）。
- 缓存层任何异常都自动回退 live，不会因数据库问题中断选股。

## 数据与降级语义

- **行情**：用 yfinance 批量下载日线；单只失败/无数据自动跳过并在 `warnings` 记录（fail-open），不中断整体。
- **LLM 重排**：未配置或调用失败时降级为纯因子排序，候选仍带技术面推荐理由。
- **DSA 增强**：默认关闭；开启后失败不影响候选输出。

## 局限

- 仅覆盖默认/自定义的有界股票池，非全量市场（美股约 1500、新加坡约 615 全主板）。
- 因子与评分复用统一的趋势引擎（均线/量价/MACD/RSI），未做各市场专属因子定制。
- 港股暂不支持选股（仅支持个股分析 / 大盘复盘的既有范围）。

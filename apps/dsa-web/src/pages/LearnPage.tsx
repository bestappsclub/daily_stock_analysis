import type React from 'react';
import { AppPage, Card, PageHeader } from '../components/common';
import { ReportMarkdownBody } from '../components/report/ReportMarkdownBody';

/**
 * 学习页：把本系统的选股 / 买卖点 / 指标 / 策略规则讲清楚。
 * 纯静态科普内容（markdown 渲染），与「选股」「问股」等页同一套布局。
 * 内容与后端趋势引擎 / 选股策略保持一致；如规则变动，更新此文件对应段落。
 */

const FLOW_MD = `
**一句话：选股看"强"，买卖看"信号 + 止损价"，仓位永远先算亏多少。**

\`\`\`
① 看大盘   大盘多头→可做多；空头→观望/轻仓
   ↓
② 选强势   用 相对强弱领涨 / 多头结构 选出跑赢大盘的票
   ↓
③ 等买点   在强势票里，等"今天出 D 点"再进，别预判
   ↓
④ 定仓位   每笔只赌总资金 1%，按 (买价−止损价) 倒推买几股
   ↓
⑤ 设止损   买入当天就记下吊灯止损价，碰到就走
   ↓
⑥ 持有/卖  没碰止损/没出 K 点就拿着，让利润奔跑
\`\`\`

> 系统存在的全部意义：**用规则代替情绪**。散户亏钱多半是"涨高了追、跌怕了割"。
`;

const SELECT_MD = `
不同行情用不同策略，别在错的行情用错的工具：

| 目的 / 行情 | 用这些策略 | 说明 |
| --- | --- | --- |
| **选强势股（趋势/牛市）** | 相对强弱领涨 \`rs_leaders\`、ADX趋势确认 \`trend_confirmed\`、趋势动量 \`momentum\`、多头趋势 \`trend_quality\`、放量突破 \`breakout\`、多头结构 \`structure_bull\` | 顺势而为，牛市主力 |
| **抄底 / 反转（超跌）** | 超跌反转 \`oversold\` | ⚠️ 逆势，风险最高，务必轻仓 |
| **择时（何时进 / 出）** | DK买点 \`dk_buy\`、DK卖点 \`dk_sell\`、向上/向下跳空 \`gap_up/gap_down\` | 不是"选股"，是"时机" |
| **判方向 / 避空头** | 多头结构 \`structure_bull\` / 空头结构 \`structure_bear\` | 当过滤器用 |

> **相对强弱领涨**经过回测验证：薄但为正的优势，且回撤控制远好于买入持有。是"选强势股"靠谱的起点。
> 其余策略尚未逐一回测验证——**没回测就别当真理**。
`;

const BUY_MD = `
**下面尽量多条共振才买（至少占 4 条）：**

1. **是强势股** —— 出现在 \`rs_leaders\`（跑赢大盘）或 \`structure_bull\`（多头结构）里
2. **买点触发** —— 当天出现 **D 点**（\`dk_signal = D\`）← 这是扣扳机的那一下
3. **没追高** —— 离 MA5 不超过 5%（\`bias_ma5 < 5%\`），涨太多就等回踩
4. **大盘多头** —— 大盘空头时整体别做多
5. **没红旗** —— 没有 OBV 顶背离、相对强弱不是 lagging

> **别预判。** "感觉它要涨"不是信号，**D 点出现**才是。
`;

const SELL_MD = `
**下面任意一条触发，立刻走，不犹豫：**

1. **跌破吊灯止损** —— 现价 < \`chandelier_stop\`（买入当天就记下这个数）
2. **出现 K 点** —— \`dk_signal = K\`
3. （预警）**OBV 顶背离** / 相对强弱由强转弱 —— 考虑减仓 / 换强势票

> **卖出不靠"感觉跌了"，靠"价格碰到那个数字"。** 止损价是买入时就定死的，不是跌了再临时找借口扛。
`;

const RULES_MD = `
比选哪只股都重要：

1. **买之前先算止损**：买价、止损价、每股亏多少 → 倒推买几股（每笔只赌总资金 **1%**）。**算不出止损就别买。**
2. **止损绝不下移**：套了不许"再等等""摊低成本"。趋势系统靠"亏小赢大"活，扛单 = 自杀。
3. **赢家让它跑**：没碰止损 / 没出 K 点就拿着，别一涨 5% 就手痒卖飞。

**仓位公式：** 每笔买入股数 = （总资金 × 单笔风险%）÷（买入价 − 止损价）
例：10 万本金、单笔赌 1%（=1000 元），50 元买、47 元止损（每股险 3 元）→ 最多买 1000 ÷ 3 ≈ 333 股。
即使止损也只亏总资金 1%，连亏 10 次仍剩 90%。
`;

const INDICATORS_MD = `
| 指标 | 字段 | 它告诉你什么 |
| --- | --- | --- |
| 均线 MA5/10/20/60 | \`ma5/ma10/ma20\` | MA5>MA10>MA20 = 多头排列 |
| 乖离率 | \`bias_ma5\` | 离均线多远，>5% 算追高 |
| MACD | \`macd_dif/dea/bar\` | 趋势确认、金叉/死叉 |
| RSI | \`rsi_6/12/24\` | 超买(>70)/超卖(<30) |
| **ADX / DMI** | \`adx/plus_di/minus_di\` | **趋势强弱**：≥25 才算有趋势，<20 是震荡（趋势策略此时易失效） |
| **OBV 量价** | \`obv/obv_divergence\` | **量价是否配合**：价涨而 OBV 走弱=顶背离(假突破警告) |
| **相对强弱 RS** | \`rs_status/rs_chg_pct\` | **个股 vs 大盘**：leading=跑赢且走强(选强汰弱的核心) |
| DK 买卖点 | \`dk_signal/dk_state\` | D点=买入信号、K点=卖出信号（唐奇安通道，东财校准） |
| 摆动结构 | \`structure\` | bull=头头高+底底高（多头）、bear=反之 |
| 跳空缺口 | \`gap_dir/gap_pct\` | 开盘相对昨收的跳空方向/幅度 |
| **吊灯止损** | \`chandelier_stop\` | **建议止损价** = HHV(close,22) − 3×ATR，棘轮只进不退 |

> 买卖不是看单一指标，是看**几个一起亮**：趋势(MA/ADX) + 选强(RS) + 量价(OBV) + 时机(DK) + 止损(吊灯)。
`;

const STRATEGY_CHEAT_MD = `
每个策略一句话——挑对行情用对工具：

| 策略 | 一句话 | 适合行情 |
| --- | --- | --- |
| 相对强弱领涨 \`rs_leaders\` | 只买跑赢大盘且走强的票 | 趋势 / 牛市（首选） |
| ADX趋势确认 \`trend_confirmed\` | 多头 + ADX≥25，趋势够强才进 | 单边趋势 |
| 趋势动量 \`momentum\` | 综合评分高、趋势向上 | 趋势 / 牛市 |
| 多头趋势 \`trend_quality\` | MA5>MA10>MA20 多头排列 | 趋势 |
| 放量突破 \`breakout\` | 放量上涨 + 买入信号 | 启动初期 |
| 多头结构 \`structure_bull\` | 头头高 + 底底高 | 判方向 / 选强 |
| DK买点 \`dk_buy\` | 当天刚出 D 点（买点） | 任何（择时用） |
| DK卖点 \`dk_sell\` | 当天刚出 K 点（卖点） | 任何（离场用） |
| 向上跳空 \`gap_up\` | 近一周开盘向上跳空 | 事件 / 启动 |
| 超跌反转 \`oversold\` | RSI 超卖、博反弹 | ⚠️ 震荡 / 超跌，逆势高风险 |

> **组合用最好**：\`rs_leaders\`/\`structure_bull\` 选出强势票 → 等其中 \`dk_buy\`（当天 D 点）触发再进。
`;

const EXAMPLE_MD = `
一只票从选到买到卖，走一遍（数字仅为示例）：

1. **大盘多头** → 可以做多。
2. **选股**：\`rs_leaders\` 扫出 NVDA —— 跑赢大盘 +20%、RS 上行。
3. **个股确认**：当天 \`dk_signal=D\`（买点）、\`adx_status=strong_trend\`、量价配合、离 MA5 仅 3%（没追高）→ 4 个绿灯，**买入**。
4. **定仓位**：账户 10 万、单笔赌 1%（=1000 元）。买价 120、吊灯止损 111（每股险 9）→ 买 1000÷9 ≈ **111 股**。
5. **设止损**：把 111 记下，当天就执行——跌破就走。
6. **持有**：股价上行，吊灯止损（\`chandelier_stop\`）随之抬高，锁住利润。
7. **卖出**：某天收盘跌破吊灯止损 / 出现 \`dk_signal=K\` → **离场**。

> 全程：**选股看强、买卖看信号、风险看止损**。情绪不参与决策。
`;

const QUICKSTART_MD = `
**网页**：左侧「选股」页 → 选市场 + 策略 → 运行 → 看候选股的买卖判定与止损价。

**命令行**（每天一张选股+买卖点表）：
\`\`\`bash
# 选强势 + 买卖判定 + 止损价
python scripts/daily_signals.py --strategy rs_leaders --top 15
# 只看今天刚出买点的票
python scripts/daily_signals.py --strategy dk_buy
# 只看自选股
python scripts/daily_signals.py --watchlist AAPL,NVDA,MSFT
\`\`\`

**灌缓存**（选股秒级、可离线）：
\`\`\`bash
python scripts/sync_prices.py --markets us   # 或 cn / sg / hk
\`\`\`
`;

const DISCLAIMER_MD = `
本页是**本系统的使用规则与通用风控原则**，**不构成投资建议**，不保证任何收益。
任何规则上实盘前，最好先用「回测」验证；实盘盈亏由你自己负责，仓位务必控制。
`;

const SECTIONS: Array<{ title: string; subtitle?: string; md: string; testId: string }> = [
  { title: '总流程：从选股到买卖', subtitle: '先看懂这张图，再看细节', md: FLOW_MD, testId: 'learn-flow' },
  { title: '一、怎么选股', subtitle: '按目的和行情挑策略', md: SELECT_MD, testId: 'learn-select' },
  { title: '二、什么时候买', subtitle: '🟢 多条共振才进', md: BUY_MD, testId: 'learn-buy' },
  { title: '三、什么时候卖', subtitle: '🔴 任一触发就走', md: SELL_MD, testId: 'learn-sell' },
  { title: '四、三条铁律 + 仓位', subtitle: '比选股更重要', md: RULES_MD, testId: 'learn-rules' },
  { title: '五、指标速查', subtitle: '每个字段在说什么', md: INDICATORS_MD, testId: 'learn-indicators' },
  { title: '六、策略速查', subtitle: '挑对行情用对策略', md: STRATEGY_CHEAT_MD, testId: 'learn-strategies' },
  { title: '七、完整买卖实例', subtitle: '从选股到止损走一遍', md: EXAMPLE_MD, testId: 'learn-example' },
  { title: '八、快速上手', subtitle: '网页 / 命令行怎么用', md: QUICKSTART_MD, testId: 'learn-quickstart' },
  { title: '免责声明', md: DISCLAIMER_MD, testId: 'learn-disclaimer' },
];

const LearnPage: React.FC = () => {
  return (
    <AppPage>
      <PageHeader
        eyebrow="学习"
        title="选股与买卖规则"
        description="看懂本系统的选股、买卖点、指标与策略——把交易从凭感觉变成照规则执行。"
      />
      <div className="space-y-4">
        {SECTIONS.map((section) => (
          <Card key={section.testId} title={section.title} subtitle={section.subtitle}>
            <ReportMarkdownBody content={section.md} testId={section.testId} />
          </Card>
        ))}
      </div>
    </AppPage>
  );
};

export default LearnPage;

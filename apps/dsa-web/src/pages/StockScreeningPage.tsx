import type React from 'react';
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, CircleAlert, Play, PlusCircle, RefreshCw, Search, Star } from 'lucide-react';
import {
  alphasiftApi,
  type AlphaSiftCandidate,
  type AlphaSiftScreenResponse,
  type AlphaSiftStrategy,
} from '../api/alphasift';
import { AppPage, Button, Collapsible, InlineAlert, Select } from '../components/common';
import { cn } from '../utils/cn';

const WATCHLIST_KEY = 'win_wl';

// 红涨绿跌（CN 习惯）：涨=红(danger)，跌=绿(success)。仅作用于涨跌方向色。
const chgClass = (value: unknown): string => {
  const v = Number(value);
  if (value == null || value === '' || Number.isNaN(v)) return 'text-secondary-text';
  if (v > 0) return 'text-danger';
  if (v < 0) return 'text-success';
  return 'text-secondary-text';
};

const formatChangePct = (value: unknown): string => {
  if (value == null || value === '' || Number.isNaN(Number(value))) return '-';
  const v = Number(value);
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
};

const MARKETS = [
  { id: 'cn', label: 'A 股' },
  { id: 'hk', label: '港股' },
  { id: 'us', label: '美股' },
  { id: 'sg', label: '新加坡' },
];

// StockScreener（姊妹项目）详情页地址；可用 VITE_STOCKSCREENER_URL 覆盖
const STOCKSCREENER_BASE =
  (import.meta.env.VITE_STOCKSCREENER_URL as string | undefined)?.replace(/\/+$/, '') ||
  'https://stockscreener-ashen.vercel.app';

// Stock Codex（Streamlit 个股分析）地址；可用 VITE_STOCK_CODEX_URL 覆盖
const STOCK_CODEX_BASE =
  (import.meta.env.VITE_STOCK_CODEX_URL as string | undefined)?.replace(/\/+$/, '') ||
  'https://stock-codex.onrender.com';

// DSA 代码 -> StockScreener 详情页 symbol：US/HK/SG 同形；A股 6 位数字补 .SS/.SZ 后缀
function toScreenerSymbol(code: string): string {
  const c = (code || '').trim().toUpperCase();
  if (/^\d{6}$/.test(c)) {
    return c.startsWith('6') || c.startsWith('9') ? `${c}.SS` : `${c}.SZ`;
  }
  return c;
}

const formatScore = (score: AlphaSiftCandidate['score']) => {
  if (score == null || Number.isNaN(Number(score))) {
    return '-';
  }
  return Number(score).toFixed(2);
};

const formatNumber = (value: unknown, digits = 2) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return Number(value).toFixed(digits);
};

const formatAmount = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  const amount = Number(value);
  if (Math.abs(amount) >= 100_000_000) {
    return `${(amount / 100_000_000).toFixed(2)} 亿`;
  }
  if (Math.abs(amount) >= 10_000) {
    return `${(amount / 10_000).toFixed(2)} 万`;
  }
  return amount.toFixed(2);
};

const formatPercent = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return `${(Number(value) * 100).toFixed(0)}%`;
};

const getCandidateReason = (item: AlphaSiftCandidate) => {
  if (item.reason) {
    return item.reason;
  }
  const summaries = item.postAnalysisSummaries || {};
  const summary = Object.values(summaries).find((value) => typeof value === 'string' && value.trim());
  if (typeof summary === 'string') {
    return summary;
  }
  return 'AlphaSift 返回候选，但没有给出文字摘要。请查看下方因子、风险和原始字段。';
};

const getSignal = (item: AlphaSiftCandidate) => {
  const rawSignal = item.raw.action ?? item.raw.signal ?? item.raw.recommendation;
  return typeof rawSignal === 'string' && rawSignal.trim() ? rawSignal : '观察';
};

const getFactorEntries = (item: AlphaSiftCandidate) =>
  Object.entries(item.factorScores || {})
    .filter(([, value]) => typeof value === 'number')
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 6);

const toMessageList = (values: string[] | undefined) =>
  Array.isArray(values) ? values.map((value) => String(value).trim()).filter(Boolean) : [];

const KNOWN_SNAPSHOT_SOURCES = new Set(['tushare', 'efinance', 'akshare_em', 'em_datacenter', 'baostock']);
const MAX_MESSAGE_DETAIL_LENGTH = 96;

const truncateMessageDetail = (value: string, maxLength = MAX_MESSAGE_DETAIL_LENGTH) => {
  const text = value.replace(/\s+/g, ' ').trim();
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
};

const summarizeAlphaSiftDiagnostic = (detail: string) => {
  if (/trade_cal returned no open trading days/i.test(detail)) {
    return '交易日历暂无可用开市日';
  }
  if (/too many requests|rate limit|http\s*429/i.test(detail)) {
    return '请求过于频繁';
  }
  if (/403 forbidden|forbidden|access denied/i.test(detail)) {
    return '访问被拒绝';
  }
  if (/timeout|timed out/i.test(detail)) {
    return '请求超时';
  }
  if (/RemoteDisconnected|Connection aborted|ProtocolError|ConnectionPool|Max retries exceeded|ProxyError|NameResolutionError/i.test(detail)) {
    return '网络连接中断';
  }
  if (/missing .*api key|GEMINI_API_KEY|GOOGLE_API_KEY|gemini_api_key/i.test(detail)) {
    return '缺少可用 LLM API Key';
  }
  if (/returned no data|empty/i.test(detail)) {
    return '未返回可用数据';
  }

  const withoutUrl = detail
    .replace(/https?:\/\/\S+/gi, 'URL')
    .replace(/\bwith url:\s*\S+/gi, 'with url: URL')
    .replace(/\burl:\s*\S+/gi, 'url: URL');
  return truncateMessageDetail(withoutUrl);
};

const parseSourceDiagnostic = (value: string) => {
  const match = value.match(/^([a-zA-Z0-9_-]+)\s*[:：]\s*(.+)$/);
  if (!match) {
    return null;
  }
  return {
    source: match[1],
    detail: match[2],
  };
};

const normalizeScreenMessageKey = (value: string) => {
  const formatted = formatScreenMessage(value);
  return formatted ? formatted.trim().toLowerCase() : value.trim().toLowerCase();
};

const formatScreenMessage = (value: string) => {
  if (/^DSA provider context applied \d+ of \d+ candidates/i.test(value)) {
    return '';
  }
  if (/^LLM ranking failed/i.test(value)) {
    return `LLM 重排失败：${summarizeAlphaSiftDiagnostic(value)}，已回退到本地因子评分。`;
  }

  const snapshotFallback = value.match(/^Snapshot source fallback:\s*(.+)$/i);
  if (snapshotFallback) {
    const parsed = parseSourceDiagnostic(snapshotFallback[1]);
    if (parsed) {
      return `数据源降级：${parsed.source}（${summarizeAlphaSiftDiagnostic(parsed.detail)}）`;
    }
    return `数据源降级：${summarizeAlphaSiftDiagnostic(snapshotFallback[1])}`;
  }

  const parsed = parseSourceDiagnostic(value);
  if (parsed && KNOWN_SNAPSHOT_SOURCES.has(parsed.source.toLowerCase())) {
    return `数据源降级：${parsed.source}（${summarizeAlphaSiftDiagnostic(parsed.detail)}）`;
  }
  return truncateMessageDetail(value);
};

const getScreenMessages = (meta: AlphaSiftScreenResponse | null) => {
  if (!meta) {
    return [];
  }
  const messages: string[] = [];
  const seen = new Set<string>();
  [...toMessageList(meta.warnings), ...toMessageList(meta.sourceErrors), ...toMessageList(meta.llmParseErrors)].forEach(
    (value) => {
      const key = normalizeScreenMessageKey(value);
      if (seen.has(key)) {
        return;
      }
      const message = formatScreenMessage(value);
      if (!message) {
        return;
      }
      seen.add(key);
      messages.push(message);
    },
  );
  return messages;
};

const ScreenAlertMessage: React.FC<{ messages: string[] }> = ({ messages }) => {
  if (messages.length <= 1) {
    return <span>{messages[0]}</span>;
  }
  return (
    <ul className="list-disc space-y-1 pl-4">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
};

const hasLlmInsight = (item: AlphaSiftCandidate) =>
  Boolean(
    item.llmThesis ||
      item.llmSector ||
      item.llmTheme ||
      item.llmConfidence != null ||
      item.llmWatchItems?.length ||
      item.llmCatalysts?.length,
  );

const StockScreeningPage: React.FC = () => {
  const [enabled, setEnabled] = useState(false);
  const [available, setAvailable] = useState(false);
  const [market, setMarket] = useState('cn');
  const [strategy, setStrategy] = useState('dual_low');
  const [strategies, setStrategies] = useState<AlphaSiftStrategy[]>([]);
  const [maxResults, setMaxResults] = useState(20);
  const [candidates, setCandidates] = useState<AlphaSiftCandidate[]>([]);
  const [screenMeta, setScreenMeta] = useState<AlphaSiftScreenResponse | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [loadingStrategies, setLoadingStrategies] = useState(false);
  const [error, setError] = useState('');
  const [strategyLoadError, setStrategyLoadError] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const [syncFull, setSyncFull] = useState(false);
  const [watchlist, setWatchlist] = useState<Set<string>>(() => {
    try {
      return new Set<string>(JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]'));
    } catch {
      return new Set<string>();
    }
  });
  const [onlyWatch, setOnlyWatch] = useState(false);

  const toggleWatch = useCallback((code: string) => {
    setWatchlist((prev) => {
      const next = new Set(prev);
      if (next.has(code)) {
        next.delete(code);
      } else {
        next.add(code);
      }
      try {
        localStorage.setItem(WATCHLIST_KEY, JSON.stringify([...next]));
      } catch {
        /* ignore quota / privacy-mode errors */
      }
      return next;
    });
  }, []);

  const selectedStrategy = useMemo(() => strategies.find((item) => item.id === strategy), [strategies, strategy]);
  const selectedStrategyTitle = selectedStrategy?.name || selectedStrategy?.title || '自定义策略';
  const displayedStrategy = selectedStrategy ? selectedStrategyTitle : `自定义策略 (${strategy})`;
  const strategyOptions = useMemo(
    () => strategies.map((item) => ({ value: item.id, label: item.name || item.title || item.id })),
    [strategies],
  );
  const visibleCandidates = useMemo(
    () => (onlyWatch ? candidates.filter((item) => watchlist.has(item.code)) : candidates),
    [candidates, onlyWatch, watchlist],
  );
  const watchCount = useMemo(
    () => candidates.filter((item) => watchlist.has(item.code)).length,
    [candidates, watchlist],
  );
  const screenMessages = useMemo(() => getScreenMessages(screenMeta), [screenMeta]);
  const llmDegraded = screenMeta?.llmRanked === false;
  const alertMessages = llmDegraded
    ? screenMessages.length > 0
      ? screenMessages
      : ['LLM 重排未完成或未返回判断，当前候选来自 AlphaSift 本地因子评分。']
    : screenMessages;
  const isScreeningEnabled = enabled && available;
  const statusText = isScreeningEnabled ? '选股已开启' : '选股未开启';

  const clearScreeningResults = () => {
    setCandidates([]);
    setScreenMeta(null);
    setExpandedCode(null);
  };

  const loadStrategies = useCallback(async (targetMarket: string) => {
    setLoadingStrategies(true);
    try {
      setStrategyLoadError('');
      const result = await alphasiftApi.getStrategies(targetMarket);
      const loadedStrategies = result.strategies || [];
      setStrategies(loadedStrategies);
      if (loadedStrategies.length > 0) {
        setStrategy((currentStrategy) =>
          loadedStrategies.some((item) => item.id === currentStrategy) ? currentStrategy : loadedStrategies[0].id,
        );
      }
    } catch (err) {
      setStrategies([]);
      setStrategyLoadError(err instanceof Error ? err.message : 'AlphaSift 策略列表加载失败');
    } finally {
      setLoadingStrategies(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    alphasiftApi
      .getStatus(market)
      .then((status) => {
        if (!active) {
          return;
        }
        setEnabled(status.enabled);
        setAvailable(status.available);
        if (status.enabled && status.available) {
          void loadStrategies(market);
        }
      })
      .catch(() => {
        if (active) {
          setEnabled(false);
          setAvailable(false);
        }
      });
    return () => {
      active = false;
    };
  }, [loadStrategies, market]);

  const handleEnable = async () => {
    setEnabling(true);
    setError('');
    try {
      await alphasiftApi.enable();
      setEnabled(true);
      setAvailable(true);
      await loadStrategies(market);
    } catch (err) {
      try {
        const status = await alphasiftApi.getStatus(market);
        setEnabled(status.enabled);
        setAvailable(status.available);
      } catch {
        setEnabled(false);
        setAvailable(false);
      }
      setError(err instanceof Error ? err.message : '开启 AlphaSift 失败');
    } finally {
      setEnabling(false);
    }
  };

  const handleStrategyChange = (nextStrategy: string) => {
    if (nextStrategy !== strategy) {
      clearScreeningResults();
    }
    setStrategy(nextStrategy);
  };

  const handleMarketChange = (nextMarket: string) => {
    if (nextMarket !== market) {
      clearScreeningResults();
    }
    setMarket(nextMarket);
  };

  const handleMaxResultsChange = (nextMaxResults: number) => {
    if (nextMaxResults !== maxResults) {
      clearScreeningResults();
    }
    setMaxResults(nextMaxResults);
  };

  // 本地行情缓存支持的原生市场（美股/新加坡/港股/A股）
  const supportsCache = market === 'us' || market === 'sg' || market === 'hk' || market === 'cn';

  const handleSyncCache = async () => {
    setSyncing(true);
    setSyncMessage('');
    setError('');
    try {
      const r = await alphasiftApi.syncCache(market, syncFull);
      setSyncMessage(
        `行情缓存已更新：${r.market} 股票池 ${r.universe} 只，刷新 ${r.refreshed}/${r.stale} 只，` +
        `新增约 ${r.savedRows} 行（${(r.elapsedMs / 1000).toFixed(1)}s）。`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : '同步行情缓存失败');
    } finally {
      setSyncing(false);
    }
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError('');
    setScreenMeta(null);
    setSyncMessage('');
    try {
      const result = await alphasiftApi.screen({ market, strategy, maxResults });
      setScreenMeta(result);
      setCandidates(result.candidates);
      setExpandedCode(result.candidates[0]?.code ?? null);
    } catch (err) {
      setCandidates([]);
      setError(err instanceof Error ? err.message : '选股失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AppPage className="max-w-6xl space-y-6 pb-12 pt-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="grid h-7 w-7 place-items-center rounded-full border-2 border-cyan text-cyan shadow-[0_0_24px_hsl(var(--primary)/0.18)]">
            <PlusCircle className="h-4 w-4" />
          </span>
          <div>
            <h1 className="text-2xl font-bold tracking-normal text-foreground">AlphaSift 选股</h1>
            <p className="mt-1 text-sm text-secondary-text">开启后通过内置 AlphaSift 适配层生成候选股票，并补充 DSA 数据与新闻</p>
          </div>
        </div>

        <div className="inline-flex w-fit items-center gap-2 rounded-2xl border border-border/70 bg-card/80 px-4 py-2 text-sm shadow-soft-card">
          <span className={`h-2.5 w-2.5 rounded-full ${isScreeningEnabled ? 'bg-success' : 'bg-warning'}`} />
          <span className="font-medium text-secondary-text">{statusText}</span>
        </div>
      </div>

      {!enabled ? (
        <InlineAlert
          variant="info"
          title="AlphaSift 未开启"
          message="点击后写入 ALPHASIFT_ENABLED=true；AlphaSift 已随后端依赖安装，若适配层缺失请先更新依赖或重建后端。"
          action={
            <Button size="sm" isLoading={enabling} loadingText="开启中..." onClick={() => void handleEnable()}>
              开启 AlphaSift
            </Button>
          }
        />
      ) : null}

      {enabled && !available ? (
        <InlineAlert
          variant="warning"
          title="AlphaSift 适配层不可用"
          message="适配层当前不可用，请先确认后端已安装依赖并重启服务，必要时执行 pip install -r requirements.txt 或使用设置页/服务端 /install 接口进行修复安装。"
        />
      ) : null}

      <InlineAlert
        variant="warning"
        title="实验功能与风险提示"
        message="AlphaSift 选股仍处于实验性质，结果仅用于研究和辅助判断，不构成投资建议；市场有风险，交易决策和损益由使用者自行承担。"
      />

      {error ? <InlineAlert variant="danger" title="调用失败" message={error} /> : null}

      <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          {/* 市场分段控件 */}
          <div className="inline-flex rounded-xl border border-border bg-surface p-1">
            {MARKETS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => handleMarketChange(item.id)}
                className={cn(
                  'rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors',
                  market === item.id ? 'bg-cyan/15 text-cyan' : 'text-secondary-text hover:text-foreground',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          {/* 规则下拉（替代策略卡片网格） */}
          <div className="min-w-56 flex-1">
            <Select
              value={selectedStrategy ? strategy : ''}
              onChange={handleStrategyChange}
              options={strategyOptions}
              searchable
              placeholder={loadingStrategies ? '正在读取规则…' : '选择选股规则'}
              searchPlaceholder="搜索规则…"
              emptyText={strategyLoadError || 'AlphaSift 规则暂未载入'}
              disabled={loadingStrategies || strategyOptions.length === 0}
            />
          </div>

          <Button
            className="h-11 min-w-32"
            isLoading={loading}
            loadingText="筛选中..."
            disabled={!isScreeningEnabled || loading}
            onClick={() => void handleSubmit()}
          >
            <Play className="h-4 w-4" />
            运行选股
          </Button>
        </div>

        {selectedStrategy?.description ? (
          <p className="mt-2.5 text-xs leading-5 text-secondary-text">{selectedStrategy.description}</p>
        ) : null}
      </section>

      <Collapsible title="高级设置">
        <div className="grid gap-4 pt-2 lg:grid-cols-[1.2fr_160px_auto] lg:items-end">
          <label className="space-y-2 text-xs font-medium text-secondary-text">
            策略参数（手动）
            <input
              className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
              value={strategy}
              onChange={(event) => handleStrategyChange(event.target.value)}
            />
          </label>

          <label className="space-y-2 text-xs font-medium text-secondary-text">
            返回数量
            <input
              className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
              type="number"
              min={1}
              max={100}
              value={maxResults}
              onChange={(event) => handleMaxResultsChange(Number(event.target.value))}
            />
          </label>

          {supportsCache && (
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                className="h-11"
                isLoading={syncing}
                loadingText="同步中..."
                disabled={syncing || loading}
                title="把该市场股票的日线行情拉取/更新到本地缓存（更快、可离线）"
                onClick={() => void handleSyncCache()}
              >
                <RefreshCw className="h-4 w-4" />
                同步行情缓存
              </Button>
              <label
                className="flex h-11 cursor-pointer items-center gap-1.5 rounded-xl border border-border bg-surface px-2.5 text-xs text-secondary-text"
                title="忽略新鲜度，重新拉取该市场全部标的的最新行情（盘内可拿到当日最新；全市场较慢）"
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 rounded border-border accent-cyan"
                  checked={syncFull}
                  onChange={(event) => setSyncFull(event.target.checked)}
                />
                强制刷新
              </label>
            </div>
          )}
        </div>
        {syncMessage && <p className="mt-3 text-xs text-success">{syncMessage}</p>}
      </Collapsible>

      <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span
              className={`grid h-7 w-7 place-items-center rounded-full ${
                candidates.length > 0 ? 'text-success' : isScreeningEnabled ? 'text-cyan' : 'text-warning'
              }`}
            >
              {candidates.length > 0 ? <CheckCircle2 className="h-5 w-5" /> : <CircleAlert className="h-5 w-5" />}
            </span>
            <div>
              <h2 className="text-sm font-semibold text-foreground">
                {candidates.length > 0 ? '选股完成' : isScreeningEnabled ? '等待运行' : '等待开启'}
              </h2>
              <p className="mt-1 text-xs text-secondary-text">
                当前策略：{displayedStrategy} · {MARKETS.find((item) => item.id === market)?.label}
              </p>
            </div>
          </div>
          <div className="grid gap-1 text-xs text-secondary-text sm:text-right">
            <span>Run ID：{screenMeta?.runId || '-'}</span>
            <span>
              快照 {screenMeta?.snapshotCount ?? '-'} · 过滤后 {screenMeta?.afterFilterCount ?? '-'} · 候选 {screenMeta?.candidateCount ?? candidates.length}
            </span>
            <span>
              LLM：{screenMeta?.llmRanked ? '已重排' : screenMeta ? '未重排' : '-'}
              {screenMeta?.llmCoverage != null ? ` · 覆盖 ${formatPercent(screenMeta.llmCoverage)}` : ''}
            </span>
            <span>
              DSA增强：{screenMeta?.dsaEnrichment?.enrichedCount ?? '-'} / {screenMeta?.dsaEnrichment?.requestedCount ?? '-'}
            </span>
          </div>
        </div>
      </section>

      {screenMeta && alertMessages.length > 0 ? (
        <InlineAlert
          variant={llmDegraded ? 'warning' : 'info'}
          title={llmDegraded ? 'LLM 已降级' : 'AlphaSift 提示'}
          message={<ScreenAlertMessage messages={alertMessages} />}
        />
      ) : null}

      <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
        <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-foreground">选股结果</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary-text">
              AlphaSift 返回候选后，DSA 会对前几名补充行情、基本面、新闻和辅助摘要。
            </p>
          </div>
          <div className="flex items-center gap-2">
            {candidates.length > 0 ? (
              <button
                type="button"
                onClick={() => setOnlyWatch((value) => !value)}
                className={cn(
                  'flex items-center gap-1.5 rounded-full border px-3 py-2 text-xs font-medium transition-colors',
                  onlyWatch
                    ? 'border-warning/40 bg-warning/10 text-warning'
                    : 'border-border bg-surface text-secondary-text hover:text-foreground',
                )}
              >
                <Star className="h-3.5 w-3.5" fill={onlyWatch ? 'currentColor' : 'none'} />
                仅看自选{watchCount > 0 ? ` (${watchCount})` : ''}
              </button>
            ) : null}
            <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-2 text-xs text-secondary-text">
              <Search className="h-4 w-4 text-cyan" />
              {visibleCandidates.length} 条候选
            </div>
          </div>
        </div>

        {visibleCandidates.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface/70 px-5 py-10 text-center">
            <p className="text-sm font-medium text-foreground">暂无结果</p>
            <p className="mt-2 text-sm text-secondary-text">
              {onlyWatch && candidates.length > 0
                ? '当前候选里没有已加入自选的股票，点上方“仅看自选”取消筛选。'
                : '开启 AlphaSift 后点击“运行选股”生成候选列表。'}
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border">
            <table className="w-full min-w-[860px] border-collapse text-sm">
              <thead className="bg-surface text-left text-xs text-secondary-text">
                <tr>
                  <th className="w-14 px-4 py-3 font-semibold">#</th>
                  <th className="px-4 py-3 font-semibold">代码</th>
                  <th className="px-4 py-3 font-semibold">名称</th>
                  <th className="px-4 py-3 font-semibold">行业</th>
                  <th className="px-4 py-3 font-semibold">价格</th>
                  <th className="px-4 py-3 font-semibold">涨跌幅</th>
                  <th className="px-4 py-3 font-semibold">评分</th>
                  <th className="px-4 py-3 font-semibold">LLM</th>
                  <th className="px-4 py-3 font-semibold">风险</th>
                  <th className="px-4 py-3 font-semibold">详情</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((item) => {
                  const expanded = expandedCode === item.code;
                  const factors = getFactorEntries(item);
                  const llmInsightAvailable = hasLlmInsight(item);
                  const llmFallbackText =
                    llmDegraded && !llmInsightAvailable
                      ? '本次 LLM 重排失败或未返回判断，当前展示的是本地因子评分结果。'
                      : '暂无 LLM 判断';
                  const dsaWarnings = item.dsaContext?.warnings || [];
                  const dsaNews = item.dsaNews || [];
                  return (
                    <Fragment key={`${item.rank}-${item.code}`}>
                      <tr className="border-t border-border align-top transition-colors hover:bg-hover/50">
                        <td className="px-4 py-3 text-secondary-text">{item.rank}</td>
                        <td className="px-4 py-3 font-mono font-semibold text-foreground">
                          <div className="flex items-center gap-2">
                            <a
                              href={`${STOCKSCREENER_BASE}/screener/${encodeURIComponent(toScreenerSymbol(item.code))}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-foreground hover:text-cyan hover:underline"
                              title="在 StockScreener 查看个股详情"
                            >
                              {item.code}
                            </a>
                            <a
                              href={`${STOCK_CODEX_BASE}/?symbol=${encodeURIComponent(toScreenerSymbol(item.code))}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs font-normal text-secondary-text hover:text-cyan hover:underline"
                              title="在 Stock Codex 分析个股"
                            >
                              分析↗
                            </a>
                          </div>
                        </td>
                        <td className="px-4 py-3 font-semibold text-foreground">{item.name || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{item.industry || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{formatNumber(item.price)}</td>
                        <td className="px-4 py-3 text-secondary-text">{formatNumber(item.changePct)}%</td>
                        <td className="px-4 py-3 font-bold text-cyan">{formatScore(item.score)}</td>
                        <td className="px-4 py-3 text-secondary-text">{llmDegraded ? '未重排' : formatScore(item.llmScore)}</td>
                        <td className="px-4 py-3">
                          <span className="rounded-lg bg-success/10 px-2.5 py-1 text-xs font-semibold text-success">
                            {item.riskLevel || 'unknown'}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <button
                            className="text-sm font-semibold text-cyan transition-colors hover:text-foreground"
                            type="button"
                            onClick={() => setExpandedCode(expanded ? null : item.code)}
                          >
                            {expanded ? '收起' : '展开查看'}
                          </button>
                        </td>
                      </tr>
                      {expanded ? (
                        <tr className="border-t border-border bg-surface/45">
                          <td colSpan={10} className="px-4 py-4">
                            <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
                              <div className="space-y-3">
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">摘要</p>
                                  <p className="mt-1 text-sm leading-6 text-foreground">{getCandidateReason(item)}</p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">操作信号</p>
                                  <p className="mt-1 text-sm text-foreground">{getSignal(item)}</p>
                                </div>
                                {item.dsaAnalysisSummary ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">DSA 增强摘要</p>
                                    <p className="mt-1 text-sm leading-6 text-foreground">{item.dsaAnalysisSummary}</p>
                                  </div>
                                ) : null}
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">LLM 判断</p>
                                  <p className="mt-1 text-sm leading-6 text-foreground">
                                    {item.llmThesis || llmFallbackText}
                                  </p>
                                  {llmInsightAvailable ? (
                                    <p className="mt-1 text-xs text-secondary-text">
                                      板块 {item.llmSector || '-'} · 主题 {item.llmTheme || '-'} · 置信度 {formatPercent(item.llmConfidence)}
                                    </p>
                                  ) : (
                                    <p className="mt-1 text-xs text-secondary-text">LLM 元数据未返回</p>
                                  )}
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">风险标签</p>
                                  <p className="mt-1 text-sm text-foreground">
                                    {[...(item.riskFlags || []), ...(item.llmRisks || [])].length
                                      ? [...(item.riskFlags || []), ...(item.llmRisks || [])].join('，')
                                      : '无'}
                                  </p>
                                </div>
                              </div>
                              <div className="space-y-3">
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">主要因子</p>
                                  <div className="mt-2 grid grid-cols-2 gap-2">
                                    {factors.length > 0 ? (
                                      factors.map(([key, value]) => (
                                        <div key={key} className="rounded-lg border border-border bg-card px-3 py-2">
                                          <span className="block text-xs text-secondary-text">{key}</span>
                                          <span className="text-sm font-semibold text-foreground">{formatNumber(value)}</span>
                                        </div>
                                      ))
                                    ) : (
                                      <span className="text-sm text-secondary-text">无因子明细</span>
                                    )}
                                  </div>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">成交额</p>
                                  <p className="mt-1 text-sm text-foreground">{formatAmount(item.amount)}</p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">LLM 关注项</p>
                                  <p className="mt-1 text-sm text-foreground">
                                    {item.llmWatchItems?.length ? item.llmWatchItems.join('，') : llmDegraded ? '未返回（LLM 已降级）' : '无'}
                                  </p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">催化因素</p>
                                  <p className="mt-1 text-sm text-foreground">
                                    {item.llmCatalysts?.length ? item.llmCatalysts.join('，') : llmDegraded ? '未返回（LLM 已降级）' : '无'}
                                  </p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">DSA 新闻</p>
                                  {dsaNews.length > 0 ? (
                                    <ul className="mt-1 space-y-1 text-sm text-foreground">
                                      {dsaNews.slice(0, 3).map((newsItem, newsIndex) => (
                                        <li key={`${item.code}-dsa-news-${newsIndex}`}>
                                          {newsItem.title || newsItem.snippet || '-'}
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="mt-1 text-sm text-secondary-text">无</p>
                                  )}
                                </div>
                                {dsaWarnings.length > 0 ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">DSA 增强提示</p>
                                    <p className="mt-1 text-sm text-secondary-text">{dsaWarnings.join('，')}</p>
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </AppPage>
  );
};

export default StockScreeningPage;

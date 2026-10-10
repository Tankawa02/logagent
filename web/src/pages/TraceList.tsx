import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { AlertTriangle, ChevronRight, Clock, Coins, MessagesSquare, Plus, Search, Wrench, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Select } from '../components/Select'
import { Badge, Card, CardHeader, Empty, ErrorBox, StatsSkeleton } from '../components/ui'
import { api } from '../lib/api'
import { formatDuration, formatGenerated, formatTokens, formatUsd, shortModel, TURN_STATUS } from '../lib/format'
import type { TraceItem } from '../lib/types'

const PAGE = 300
const LIMIT_MAX = 2000

type Measured = TraceItem & { elapsed_seconds: number; usage: NonNullable<TraceItem['usage']> }

/** 旧版恢复的轮次没有保存耗时与用量，统计平均值时跳过，不能当成 0 */
function isMeasured(item: TraceItem): item is Measured {
  return item.elapsed_seconds != null && item.usage != null
}

const SELECT =
  'rounded-md border border-zinc-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-800 dark:bg-zinc-950'

function Stat({
  icon: Icon,
  label,
  value,
  hint,
  empty,
}: {
  icon: typeof Clock
  label: string
  value: string
  hint?: string
  empty?: boolean
}) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        {label}
      </div>
      <div
        className={`mt-1 font-mono text-xl font-semibold tabular-nums ${
          empty ? 'text-zinc-300 dark:text-zinc-600' : 'text-zinc-900 dark:text-zinc-50'
        }`}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 truncate text-xs text-zinc-500 dark:text-zinc-400">{empty ? '暂无数据' : hint}</div>}
    </div>
  )
}

const TRACE_FEATURES = [
  { icon: Clock, title: '耗时', text: '每一轮从提问到出报告用了多久' },
  { icon: Coins, title: 'Tokens', text: '模型消耗，便于估算成本' },
  { icon: Wrench, title: '工具调用', text: '读了哪些日志、搜了哪些代码、哪一步失败' },
]

function TraceEmpty({ session }: { session?: string }) {
  return (
    <Card className="px-6 py-12 text-center">
      <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-100 dark:bg-brand-950/40 dark:text-brand-300 dark:ring-brand-900">
        <MessagesSquare className="h-5 w-5" aria-hidden />
      </span>
      <h2 className="mt-4 text-base font-semibold text-zinc-900 dark:text-zinc-50">
        {session ? '这个会话还没有完成的对话' : '还没有执行记录'}
      </h2>
      <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
        {session
          ? '在会话里提问并等这一轮结束后，这里会出现它的耗时和工具调用。'
          : '完成一次日志分析后，这里会按轮次记录模型的执行过程，方便你回看和比较。'}
      </p>
      <ul className="mx-auto mt-6 grid max-w-xl gap-3 text-left sm:grid-cols-3">
        {TRACE_FEATURES.map(({ icon: Icon, title, text }) => (
          <li key={title} className="rounded-lg border border-zinc-200/80 px-3 py-2.5 dark:border-zinc-800">
            <span className="flex items-center gap-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200">
              <Icon className="h-3.5 w-3.5 text-zinc-400" aria-hidden />
              {title}
            </span>
            <span className="mt-1 block text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">{text}</span>
          </li>
        ))}
      </ul>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {session ? (
          <Link
            to="/sessions/$name"
            params={{ name: session }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400"
          >
            回到会话
          </Link>
        ) : (
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400"
          >
            <Plus className="h-4 w-4" aria-hidden />
            新建分析
          </Link>
        )}
      </div>
    </Card>
  )
}

function ToolRanking({ items }: { items: TraceItem[] }) {
  const ranking = useMemo(() => {
    const map = new Map<string, number>()
    for (const item of items) for (const [name, count] of Object.entries(item.tools)) map.set(name, (map.get(name) ?? 0) + count)
    return [...map.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  }, [items])
  const max = ranking[0]?.[1] ?? 1
  return (
    <Card>
      <CardHeader title="工具使用排行" />
      {ranking.length === 0 ? (
        <Empty>还没有工具调用</Empty>
      ) : (
        <ul className="space-y-2 px-4 py-3">
          {ranking.map(([name, count]) => (
            <li key={name} className="text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate font-mono text-xs text-zinc-700 dark:text-zinc-300">{name}</span>
                <span className="shrink-0 font-mono text-xs tabular-nums text-zinc-500">{count}</span>
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800">
                <div className="h-full rounded-full bg-amber-500" style={{ width: `${(count / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ModelBreakdown({ items }: { items: TraceItem[] }) {
  const rows = useMemo(() => {
    const map = new Map<string, { turns: number; measured: number; seconds: number; tokens: number; cost: number; priced: number }>()
    for (const item of items) {
      const key = shortModel(item.model)
      const row = map.get(key) ?? { turns: 0, measured: 0, seconds: 0, tokens: 0, cost: 0, priced: 0 }
      row.turns += 1
      if (isMeasured(item)) {
        row.measured += 1
        row.seconds += item.elapsed_seconds
        row.tokens += item.usage.total
        if (item.cost_usd != null) {
          row.priced += 1
          row.cost += item.cost_usd
        }
      }
      map.set(key, row)
    }
    return [...map.entries()].sort((a, b) => b[1].turns - a[1].turns)
  }, [items])
  return (
    <Card>
      <CardHeader title="按模型" />
      {rows.length === 0 ? (
        <Empty>暂无数据</Empty>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {rows.map(([model, row]) => (
            <li key={model} className="px-4 py-2.5 text-sm">
              <div className="truncate font-mono text-xs text-zinc-800 dark:text-zinc-200" title={model}>
                {model || '未知模型'}
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs tabular-nums text-zinc-500">
                <span>{row.turns} 轮</span>
                {row.measured ? (
                  <>
                    <span>平均 {formatDuration(row.seconds / row.measured)}</span>
                    <span>平均 {formatTokens(row.tokens / row.measured)} tokens</span>
                    {row.priced > 0 && <span>平均 {formatUsd(row.cost / row.priced)}/轮</span>}
                  </>
                ) : (
                  <span>耗时与用量未记录</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function TraceRow({ item, maxSeconds }: { item: TraceItem; maxSeconds: number }) {
  const status = TURN_STATUS[item.status] ?? TURN_STATUS.ok
  return (
    <li>
      <Link
        to="/trace/$name/$turn"
        params={{ name: item.session, turn: String(item.turn) }}
        className="group flex items-center gap-3 px-4 py-3 hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge tone={status.tone}>{status.label}</Badge>
            <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">{item.question || '（无问题文本）'}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-zinc-500">
            <span className="tabular-nums">{formatGenerated(item.generated_at)}</span>
            <span className="truncate">
              {item.title || item.session} · 第 {item.turn} 轮
            </span>
            <span className="font-mono">{shortModel(item.model)}</span>
            {item.failed_tools > 0 && <span className="text-red-600">{item.failed_tools} 次工具失败</span>}
            {item.incomplete_tools > 0 && <span className="text-amber-600">{item.incomplete_tools} 个工具未完成</span>}
            {item.legacy && <span>旧版记录</span>}
          </div>
          <div className="mt-2 flex items-center gap-3 text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <div className="h-1.5 min-w-0 flex-1 rounded-full bg-zinc-100 dark:bg-zinc-800" aria-hidden>
                {item.elapsed_seconds != null && (
                  <div
                    className={`h-full rounded-full ${item.status === 'error' ? 'bg-red-500' : 'bg-brand-500'}`}
                    style={{ width: `${Math.max(2, (item.elapsed_seconds / maxSeconds) * 100)}%` }}
                  />
                )}
              </div>
              <span className="w-14 shrink-0 text-right font-mono" title={item.elapsed_seconds == null ? '未记录耗时' : undefined}>
                {item.elapsed_seconds == null ? '—' : formatDuration(item.elapsed_seconds)}
              </span>
            </div>
            <span className="flex w-14 shrink-0 items-center gap-1 font-mono" title={item.usage ? 'tokens' : '未记录用量'}>
              <Coins className="h-3 w-3" aria-hidden />
              {item.usage ? formatTokens(item.usage.total) : '—'}
            </span>
            <span
              className={`flex w-10 shrink-0 items-center gap-1 font-mono ${item.failed_tools ? 'text-red-600' : ''}`}
              title={item.failed_tools ? `工具调用 ${item.tool_count} 次，${item.failed_tools} 次失败` : `工具调用 ${item.tool_count} 次`}
            >
              <Wrench className="h-3 w-3" aria-hidden />
              {item.tool_count}
            </span>
          </div>
        </div>
        <ChevronRight className="h-4 w-4 shrink-0 text-zinc-300 group-hover:text-zinc-500" aria-hidden />
      </Link>
    </li>
  )
}

export function TraceList({ session }: { session?: string }) {
  const [text, setText] = useState('')
  const [model, setModel] = useState('')
  const [status, setStatus] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const trace = useQuery({
    queryKey: ['trace', session ?? '', limit],
    queryFn: () => api.trace(session, limit),
    placeholderData: (previous) => previous,
  })

  const all = useMemo(() => trace.data ?? [], [trace.data])
  // 返回条数顶到上限，说明更早的轮次没取回来：统计只覆盖最近这些
  const capped = all.length >= limit
  // 同一个模型可能带不同的 provider 前缀（openai:kimi-k3 / kimi-k3），按去掉前缀后的名字归为一类
  const models = useMemo(() => [...new Set(all.map((t) => shortModel(t.model)))].sort(), [all])
  const items = useMemo(() => {
    const needle = text.trim().toLowerCase()
    return all.filter(
      (t) =>
        (!model || shortModel(t.model) === model) &&
        (!status || t.status === status) &&
        (!needle || `${t.question} ${t.title} ${t.session}`.toLowerCase().includes(needle)),
    )
  }, [all, text, model, status])

  const totals = useMemo(() => {
    const measured = items.filter(isMeasured)
    const seconds = measured.reduce((sum, t) => sum + t.elapsed_seconds, 0)
    const tokens = measured.reduce((sum, t) => sum + t.usage.total, 0)
    const priced = measured.filter((t) => t.cost_usd != null)
    const cost = priced.reduce((sum, t) => sum + (t.cost_usd ?? 0), 0)
    const tools = items.reduce((sum, t) => sum + t.tool_count, 0)
    const failed = items.reduce((sum, t) => sum + t.failed_tools, 0)
    const errors = items.filter((t) => t.status !== 'ok').length
    return {
      measured: measured.length,
      unmeasured: items.length - measured.length,
      seconds,
      tokens,
      cost,
      priced: priced.length,
      tools,
      failed,
      errors,
    }
  }, [items])
  const maxSeconds = Math.max(1, ...items.map((t) => t.elapsed_seconds ?? 0))
  const unmeasuredHint = totals.unmeasured ? ` · ${totals.unmeasured} 轮旧记录未计入` : ''
  const showModels = new Set(items.map((t) => shortModel(t.model))).size > 1
  const showTools = items.some((t) => Object.keys(t.tools).length > 0)

  return (
    <main className="h-full overflow-auto">
      <div className="mx-auto max-w-5xl space-y-5 px-4 py-6 sm:px-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">执行记录</h1>
            <p className="mt-1 text-sm text-zinc-500">每一轮对话用了哪个模型、耗时多少、调用了哪些工具。</p>
          </div>
          {session && (
            <Link
              to="/trace"
              className="inline-flex items-center gap-1 rounded-md bg-zinc-100 px-2 py-1 text-xs text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300"
            >
              仅看会话 <span className="font-mono">{session}</span>
              <X className="h-3 w-3" aria-label="清除会话筛选" />
            </Link>
          )}
        </header>

        {trace.isLoading && <StatsSkeleton label="加载执行记录" />}
        {trace.error && <ErrorBox error={trace.error} />}

        {trace.data && all.length === 0 && <TraceEmpty session={session} />}
        {trace.data && all.length > 0 && (
          <>
            {capped && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300">
                <span>仅统计最近 {all.length} 轮，更早的对话没有计入下面的数字。</span>
                {limit < LIMIT_MAX && (
                  <button
                    type="button"
                    onClick={() => setLimit((n) => Math.min(n + PAGE, LIMIT_MAX))}
                    disabled={trace.isFetching}
                    className="rounded-md bg-white px-2 py-1 text-xs font-medium text-amber-900 ring-1 ring-amber-300 hover:bg-amber-100 disabled:opacity-60 dark:bg-amber-900/40 dark:text-amber-200 dark:ring-amber-800"
                  >
                    {trace.isFetching ? '加载中…' : `再加载 ${PAGE} 轮`}
                  </button>
                )}
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat
                icon={MessagesSquare}
                empty={!items.length}
                label={capped ? '最近对话轮数' : '对话轮数'}
                value={String(items.length)}
                hint={totals.errors ? `${totals.errors} 轮未正常完成` : '全部正常完成'}
              />
              <Stat
                icon={Clock}
                empty={!totals.measured}
                label="平均耗时"
                value={totals.measured ? formatDuration(totals.seconds / totals.measured) : '—'}
                hint={`合计 ${formatDuration(totals.seconds)}${unmeasuredHint}`}
              />
              <Stat
                icon={Coins}
                empty={!totals.measured}
                label="tokens"
                value={formatTokens(totals.tokens)}
                hint={`平均每轮 ${totals.measured ? formatTokens(totals.tokens / totals.measured) : '—'}${
                  totals.priced ? ` · 费用 ${formatUsd(totals.cost)}${totals.priced < totals.measured ? '+' : ''}` : ''
                }${unmeasuredHint}`}
              />
              <Stat
                icon={totals.failed ? AlertTriangle : Wrench}
                label="工具调用"
                empty={!items.length}
                value={String(totals.tools)}
                hint={totals.failed ? `${totals.failed} 次失败` : '无失败'}
              />
            </div>

            <div className={`grid gap-5 ${showModels || showTools ? 'lg:grid-cols-[1fr_16rem]' : ''}`}>
              <Card className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 border-b border-zinc-100 px-4 py-2.5 dark:border-zinc-800">
                  <div className="relative min-w-40 flex-1">
                    <Search
                      className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400"
                      aria-hidden
                    />
                    <input
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      placeholder="搜索问题或会话"
                      aria-label="搜索问题或会话"
                      className={`${SELECT} w-full pl-8`}
                    />
                  </div>
                  <Select
                    value={model}
                    onChange={setModel}
                    ariaLabel="按模型筛选"
                    className="w-40 max-w-48"
                    options={[{ value: '', label: '全部模型' }, ...models.map((m) => ({ value: m, label: shortModel(m), title: m }))]}
                  />
                  <Select
                    value={status}
                    onChange={setStatus}
                    ariaLabel="按状态筛选"
                    align="right"
                    className="w-32"
                    options={[
                      { value: '', label: '全部状态' },
                      ...Object.entries(TURN_STATUS).map(([key, s]) => ({ value: key, label: s.label })),
                    ]}
                  />
                </div>
                {items.length > 0 && (
                  <p className="flex items-center gap-1.5 border-b border-zinc-100 px-4 py-1.5 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
                    <span className="h-1.5 w-6 rounded-full bg-brand-500" aria-hidden />
                    条形长度为相对耗时，最长的一轮为满格
                  </p>
                )}
                {items.length === 0 ? (
                  <Empty>{all.length ? '没有符合筛选条件的对话' : '还没有对话记录，先去新建一次分析吧'}</Empty>
                ) : (
                  <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
                    {items.map((item) => (
                      <TraceRow key={`${item.session}-${item.turn}`} item={item} maxSeconds={maxSeconds} />
                    ))}
                  </ul>
                )}
              </Card>
              {/* 只用了一个模型、也没有工具调用时，右栏的两张卡片都只是重复或空白，干脆收起 */}
              {(showModels || showTools) && (
                <div className="space-y-5">
                  {showModels && <ModelBreakdown items={items} />}
                  {showTools && <ToolRanking items={items} />}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </main>
  )
}

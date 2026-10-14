import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { ArrowDownUp, Check, ChevronDown, Clock, Coins, Hash, MessagesSquare, Plus, Search, Wrench, X } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../components/Menu'
import { TopBar, TopBarTitle } from '../components/TopBar'
import { STATUS_STYLE, TraceStatusBadge } from '../components/TraceStatusBadge'
import { buttonClass, Card, Empty, ErrorBox, StatsSkeleton } from '../components/ui'
import { api } from '../lib/api'
import { formatDayTime, formatDuration, formatGenerated, formatTokens, formatUsd, isToday, shortModel, TURN_STATUS } from '../lib/format'
import type { TraceItem } from '../lib/types'

const PAGE = 300
const LIMIT_MAX = 2000

type Sort = 'newest' | 'oldest' | 'slowest' | 'tokens' | 'tools'

const SORT_LABELS: Record<Sort, string> = {
  newest: '最新优先',
  oldest: '最早优先',
  slowest: '耗时最长',
  tokens: 'Token 最多',
  tools: '工具最多',
}

const SORTERS: Record<Sort, (a: TraceItem, b: TraceItem) => number> = {
  newest: (a, b) => (b.generated_at ?? '').localeCompare(a.generated_at ?? ''),
  oldest: (a, b) => (a.generated_at ?? '').localeCompare(b.generated_at ?? ''),
  slowest: (a, b) => (b.elapsed_seconds ?? -1) - (a.elapsed_seconds ?? -1),
  tokens: (a, b) => (b.usage?.total ?? -1) - (a.usage?.total ?? -1),
  tools: (a, b) => b.tool_count - a.tool_count,
}

const TRIGGER =
  'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-input bg-background px-2.5 text-[13px] text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

function percentile(values: number[], p: number): number {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)]
}

type Measured = TraceItem & { elapsed_seconds: number; usage: NonNullable<TraceItem['usage']> }

/** 旧版恢复的轮次没有保存耗时与用量，统计平均值时跳过，不能当成 0 */
function isMeasured(item: TraceItem): item is Measured {
  return item.elapsed_seconds != null && item.usage != null
}

function Stat({
  icon: Icon,
  label,
  value,
  hint,
  empty,
  danger,
}: {
  icon: typeof Clock
  label: string
  value: string
  hint?: string
  empty?: boolean
  danger?: boolean
}) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3.5">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" aria-hidden />
        {label}
      </div>
      <div
        className={`mt-1.5 text-2xl font-semibold tabular-nums tracking-tight ${empty ? 'text-muted-foreground/50' : 'text-foreground'}`}
      >
        {value}
      </div>
      {hint && (
        <div className={`mt-0.5 truncate text-xs ${danger ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'}`} title={hint}>
          {empty ? '暂无数据' : hint}
        </div>
      )}
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
      <span className="mx-auto flex size-11 items-center justify-center rounded-full bg-brand-soft text-brand-strong">
        <MessagesSquare className="size-5" aria-hidden />
      </span>
      <h2 className="mt-4 text-base font-semibold text-foreground">{session ? '这个会话还没有完成的对话' : '还没有执行记录'}</h2>
      <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-muted-foreground">
        {session
          ? '在会话里提问并等这一轮结束后，这里会出现它的耗时和工具调用。'
          : '完成一次日志分析后，这里会按轮次记录模型的执行过程，方便你回看和比较。'}
      </p>
      <ul className="mx-auto mt-6 grid max-w-xl gap-3 text-left sm:grid-cols-3">
        {TRACE_FEATURES.map(({ icon: Icon, title, text }) => (
          <li key={title} className="rounded-lg border border-border px-3 py-2.5">
            <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
              <Icon className="size-3.5 text-muted-foreground" aria-hidden />
              {title}
            </span>
            <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{text}</span>
          </li>
        ))}
      </ul>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {session ? (
          <Link to="/sessions/$name" params={{ name: session }} className={buttonClass('primary')}>
            回到会话
          </Link>
        ) : (
          <Link to="/" className={buttonClass('primary')}>
            <Plus className="size-4" aria-hidden />
            新建分析
          </Link>
        )}
      </div>
    </Card>
  )
}

function SideCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h2 className="mb-3 text-[13px] font-semibold text-foreground">{title}</h2>
      {children}
    </section>
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
    <SideCard title="常用工具">
      {ranking.length === 0 ? (
        <p className="text-sm text-muted-foreground">还没有工具调用</p>
      ) : (
        <ul className="space-y-2.5">
          {ranking.map(([name, count]) => (
            <li key={name} className="flex items-center gap-2.5 text-xs">
              <span className="w-28 shrink-0 truncate font-mono text-foreground/90" title={name}>
                {name}
              </span>
              <span className="h-1.5 min-w-0 flex-1 rounded-full bg-muted" aria-hidden>
                <span className="block h-full rounded-full bg-amber-500" style={{ width: `${(count / max) * 100}%` }} />
              </span>
              <span className="w-6 shrink-0 text-right tabular-nums text-muted-foreground">{count}</span>
            </li>
          ))}
        </ul>
      )}
    </SideCard>
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
  const max = rows[0]?.[1].turns ?? 1
  return (
    <SideCard title="模型分布">
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无数据</p>
      ) : (
        <ul className="space-y-3">
          {rows.map(([model, row]) => (
            <li key={model}>
              <div className="flex items-baseline justify-between gap-2 text-[13px]">
                <span className="truncate font-mono text-xs text-foreground/90" title={model}>
                  {model || '未知模型'}
                </span>
                <span
                  className="shrink-0 tabular-nums text-muted-foreground"
                  title={
                    row.measured
                      ? `平均 ${formatTokens(row.tokens / row.measured)} tokens${row.priced ? ` · 平均 ${formatUsd(row.cost / row.priced)}/轮` : ''}`
                      : '耗时与用量未记录'
                  }
                >
                  {row.turns} 次{row.measured ? ` · 平均 ${formatDuration(row.seconds / row.measured)}` : ''}
                </span>
              </div>
              <div className="mt-1.5 h-1.5 rounded-full bg-muted" aria-hidden>
                <div className="h-full rounded-full bg-primary" style={{ width: `${(row.turns / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </SideCard>
  )
}

const TH = 'whitespace-nowrap px-3 py-2.5 text-left text-xs font-medium text-muted-foreground'

function TraceTable({ items, maxSeconds }: { items: TraceItem[]; maxSeconds: number }) {
  const navigate = useNavigate()
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <thead className="border-b border-border bg-muted/40">
          <tr>
            <th scope="col" className={`${TH} pl-4`}>
              状态
            </th>
            <th scope="col" className={TH}>
              问题
            </th>
            <th scope="col" className={TH}>
              模型
            </th>
            <th scope="col" className={TH}>
              耗时
            </th>
            <th scope="col" className={`${TH} text-right`}>
              Token
            </th>
            <th scope="col" className={`${TH} text-right`}>
              工具
            </th>
            <th scope="col" className={`${TH} pr-4 text-right`}>
              时间
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((item) => {
            const style = STATUS_STYLE[item.status] ?? STATUS_STYLE.ok
            const to = { to: '/trace/$name/$turn', params: { name: item.session, turn: String(item.turn) } } as const
            return (
              <tr
                key={`${item.session}-${item.turn}`}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('a')) return
                  void navigate(to)
                }}
                className="cursor-pointer transition-colors hover:bg-accent/60"
              >
                <td className="py-3 pl-4 pr-3 align-middle">
                  <TraceStatusBadge status={item.status} />
                </td>
                <td className="w-full max-w-0 px-3 py-3 align-middle">
                  <Link
                    {...to}
                    className="block truncate font-medium text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {item.question || '（无问题文本）'}
                  </Link>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                    {item.title || item.session} · 第 {item.turn} 轮{item.legacy ? ' · 旧版记录' : ''}
                  </span>
                </td>
                <td className="whitespace-nowrap px-3 py-3 align-middle font-mono text-xs text-muted-foreground" title={item.model}>
                  {shortModel(item.model)}
                </td>
                <td className="px-3 py-3 align-middle">
                  <div className="flex items-center gap-2.5">
                    <span className="h-1.5 w-12 shrink-0 rounded-full bg-muted" aria-hidden>
                      {item.elapsed_seconds != null && (
                        <span
                          className={`block h-full rounded-full ${style.bar}`}
                          style={{ width: `${Math.max(4, (item.elapsed_seconds / maxSeconds) * 100)}%` }}
                        />
                      )}
                    </span>
                    <span
                      className="w-14 text-right tabular-nums text-foreground"
                      title={item.elapsed_seconds == null ? '未记录耗时' : undefined}
                    >
                      {item.elapsed_seconds == null ? '—' : formatDuration(item.elapsed_seconds)}
                    </span>
                  </div>
                </td>
                <td
                  className="whitespace-nowrap px-3 py-3 text-right align-middle tabular-nums text-foreground"
                  title={item.usage ? 'tokens' : '未记录用量'}
                >
                  {item.usage ? formatTokens(item.usage.total) : '—'}
                </td>
                <td
                  className="whitespace-nowrap px-3 py-3 text-right align-middle tabular-nums text-foreground"
                  title={
                    item.failed_tools ? `工具调用 ${item.tool_count} 次，${item.failed_tools} 次失败` : `工具调用 ${item.tool_count} 次`
                  }
                >
                  {item.tool_count}
                  {item.failed_tools > 0 && <span className="ml-1 text-xs text-red-600 dark:text-red-400">{item.failed_tools} 失败</span>}
                  {item.incomplete_tools > 0 && (
                    <span className="ml-1 text-xs text-amber-600 dark:text-amber-400">{item.incomplete_tools} 未完成</span>
                  )}
                </td>
                <td
                  className="whitespace-nowrap py-3 pl-3 pr-4 text-right align-middle text-xs tabular-nums text-muted-foreground"
                  title={formatGenerated(item.generated_at)}
                >
                  {formatDayTime(item.generated_at)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function TraceList({ session }: { session?: string }) {
  const [text, setText] = useState('')
  const [model, setModel] = useState('')
  const [status, setStatus] = useState('')
  const [sort, setSort] = useState<Sort>('newest')
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
  // 状态标签上的数字按除状态以外的筛选条件计算，切换状态时数字不跟着变
  const filtered = useMemo(() => {
    const needle = text.trim().toLowerCase()
    return all.filter(
      (t) =>
        (!model || shortModel(t.model) === model) && (!needle || `${t.question} ${t.title} ${t.session}`.toLowerCase().includes(needle)),
    )
  }, [all, text, model])
  const items = useMemo(
    () => (status ? filtered.filter((t) => t.status === status) : [...filtered]).sort(SORTERS[sort]),
    [filtered, status, sort],
  )
  const counts = useMemo(() => {
    const map: Record<string, number> = {}
    for (const t of filtered) map[t.status] = (map[t.status] ?? 0) + 1
    return map
  }, [filtered])

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
      today: items.filter((t) => isToday(t.generated_at)).length,
      p95: percentile(
        measured.map((t) => t.elapsed_seconds),
        95,
      ),
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
  const tabs = [
    { value: '', label: '全部', count: filtered.length },
    ...Object.entries(TURN_STATUS).map(([key, s]) => ({ value: key, label: s.label, count: counts[key] ?? 0 })),
  ]

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar
        actions={
          session && (
            <Link to="/trace" className={buttonClass('secondary', 'xs', 'max-w-56')}>
              <span className="truncate">
                仅看会话 <span className="font-mono">{session}</span>
              </span>
              <X className="size-3 shrink-0" aria-label="清除会话筛选" />
            </Link>
          )
        }
      >
        <TopBarTitle>执行记录</TopBarTitle>
      </TopBar>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
          <p className="max-w-2xl text-sm leading-6 text-muted-foreground">每次分析中模型和工具的调用明细。用来排查慢、贵或失败的执行。</p>

          {trace.isLoading && <StatsSkeleton label="加载执行记录" />}
          {trace.error && <ErrorBox error={trace.error} />}

          {trace.data && all.length === 0 && <TraceEmpty session={session} />}
          {trace.data && all.length > 0 && (
            <>
              {capped && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                  <span>仅统计最近 {all.length} 轮，更早的对话没有计入下面的数字。</span>
                  {limit < LIMIT_MAX && (
                    <button
                      type="button"
                      onClick={() => setLimit((n) => Math.min(n + PAGE, LIMIT_MAX))}
                      disabled={trace.isFetching}
                      className={buttonClass('secondary', 'xs')}
                    >
                      {trace.isFetching ? '加载中…' : `再加载 ${PAGE} 轮`}
                    </button>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <Stat
                  icon={Hash}
                  empty={!items.length}
                  label={capped ? '最近执行次数' : '执行次数'}
                  value={String(items.length)}
                  hint={`今天 ${totals.today} 次${totals.errors ? ` · ${totals.errors} 次未正常完成` : ''}`}
                />
                <Stat
                  icon={Clock}
                  empty={!totals.measured}
                  label="平均耗时"
                  value={totals.measured ? formatDuration(totals.seconds / totals.measured) : '—'}
                  hint={`P95 ${formatDuration(totals.p95)}${unmeasuredHint}`}
                />
                <Stat
                  icon={Coins}
                  empty={!totals.measured}
                  label="Token 消耗"
                  value={formatTokens(totals.tokens)}
                  hint={`${
                    totals.priced
                      ? `费用约 ${formatUsd(totals.cost)}${totals.priced < totals.measured ? '+' : ''}`
                      : `平均每次 ${totals.measured ? formatTokens(totals.tokens / totals.measured) : '—'}`
                  }${unmeasuredHint}`}
                />
                <Stat
                  icon={Wrench}
                  label="工具调用"
                  empty={!items.length}
                  value={String(totals.tools)}
                  danger={totals.failed > 0}
                  hint={
                    totals.failed
                      ? `${totals.failed} 次失败 · ${((totals.failed / Math.max(1, totals.tools)) * 100).toFixed(1)}%`
                      : '无失败'
                  }
                />
              </div>

              <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
                <div className="min-w-0 space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <div role="tablist" aria-label="按状态筛选" className="inline-flex items-center rounded-lg bg-muted p-0.5">
                      {tabs.map((tab) => {
                        const active = status === tab.value
                        return (
                          <button
                            key={tab.value || 'all'}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            onClick={() => setStatus(tab.value)}
                            className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                              active ? 'bg-card font-medium text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                            }`}
                          >
                            {tab.label}
                            <span className={`tabular-nums ${tab.value === 'error' && tab.count ? 'text-red-600 dark:text-red-400' : ''}`}>
                              {tab.count}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                    <div className="ml-auto flex min-w-0 flex-1 items-center justify-end gap-2">
                      <label className="relative min-w-40 max-w-80 flex-1">
                        <span className="sr-only">搜索问题或会话</span>
                        <Search
                          className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                          aria-hidden
                        />
                        <input
                          value={text}
                          onChange={(e) => setText(e.target.value)}
                          placeholder="搜索问题或会话"
                          className="h-8 w-full rounded-lg border border-input bg-background pl-8 pr-8 text-[13px] outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
                        />
                        {text && (
                          <button
                            type="button"
                            onClick={() => setText('')}
                            aria-label="清除搜索"
                            className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                          >
                            <X className="size-3.5" aria-hidden />
                          </button>
                        )}
                      </label>
                      <DropdownMenu>
                        <DropdownMenuTrigger className={TRIGGER} aria-label="按模型筛选">
                          <span className="text-muted-foreground">模型</span>
                          <span className={`max-w-36 truncate ${model ? 'font-mono text-xs' : ''}`}>{model || '全部'}</span>
                          <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-56">
                          <DropdownMenuItem onSelect={() => setModel('')}>
                            <Check className={model ? 'opacity-0' : ''} aria-hidden />
                            全部模型
                          </DropdownMenuItem>
                          {models.length > 0 && <DropdownMenuSeparator />}
                          {models.map((m) => (
                            <DropdownMenuItem key={m} onSelect={() => setModel(m)}>
                              <Check className={model === m ? '' : 'opacity-0'} aria-hidden />
                              <span className="truncate font-mono text-xs">{m}</span>
                            </DropdownMenuItem>
                          ))}
                        </DropdownMenuContent>
                      </DropdownMenu>
                      <DropdownMenu>
                        <DropdownMenuTrigger className={TRIGGER} aria-label="排序方式">
                          <ArrowDownUp className="size-3.5 text-muted-foreground" aria-hidden />
                          {SORT_LABELS[sort]}
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-40">
                          {(Object.keys(SORT_LABELS) as Sort[]).map((s) => (
                            <DropdownMenuItem key={s} onSelect={() => setSort(s)}>
                              <Check className={sort === s ? '' : 'opacity-0'} aria-hidden />
                              {SORT_LABELS[s]}
                            </DropdownMenuItem>
                          ))}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </div>
                  {items.length === 0 ? (
                    <Card>
                      <Empty>{all.length ? '没有符合筛选条件的对话' : '还没有对话记录，先去新建一次分析吧'}</Empty>
                    </Card>
                  ) : (
                    <TraceTable items={items} maxSeconds={maxSeconds} />
                  )}
                  {items.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      显示 {items.length} 条，共 {all.length} 条
                    </p>
                  )}
                </div>
                <aside className="grid content-start gap-4 sm:grid-cols-2 lg:grid-cols-1">
                  <ModelBreakdown items={items} />
                  <ToolRanking items={items} />
                </aside>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

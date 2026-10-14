import { ChevronRight, ChevronsDownUp, ChevronsUpDown, Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { formatDuration, formatTokens } from '../lib/format'
import { spanMeta, type Span } from './TraceSpanDetail'

export interface SpanGroup {
  span: Span
  children: Span[]
}

/** 按 LangSmith 的父子结构组织：模型调用为父节点，它发起的工具调用挂在下面 */
export function groupSpans(spans: Span[]): SpanGroup[] {
  const groups: SpanGroup[] = []
  const byRequestId = new Map<string, SpanGroup>()
  let current: SpanGroup | null = null
  for (const span of spans) {
    if (span.kind === 'llm') {
      current = { span, children: [] }
      groups.push(current)
      for (const req of span.call.tool_requests ?? []) if (req.id) byRequestId.set(req.id, current)
      continue
    }
    const owner = (span.call.call_id && byRequestId.get(span.call.call_id)) || (span.call.call_id ? null : current)
    if (owner) owner.children.push(span)
    else groups.push({ span, children: [] })
  }
  return groups
}

/** 一秒以内显示毫秒，避免一列 0.0s 看不出差别 */
export function spanDuration(seconds: number): string {
  if (seconds > 0 && seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))}ms`
  return formatDuration(seconds)
}

/** 名称列自适应，耗时与时间条固定宽度：缩进只压缩名称列，各层级的时间条才能上下对齐 */
const COLUMNS = 'grid grid-cols-[minmax(0,1fr)_3rem_7rem] items-center gap-2 xl:grid-cols-[minmax(0,1fr)_3rem_10rem]'

const TICK_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600]

function axisTicks(total: number): number[] {
  const step = TICK_STEPS.find((s) => total / s <= 4) ?? TICK_STEPS[TICK_STEPS.length - 1]
  return Array.from({ length: Math.floor(total / step) + 1 }, (_, i) => i * step)
}

function tickLabel(seconds: number): string {
  if (seconds === 0) return '0s'
  if (seconds < 60) return `${Number(seconds.toFixed(1))}s`
  return seconds % 60 ? `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s` : `${seconds / 60}m`
}

function searchText(span: Span): string {
  if (span.kind === 'llm') {
    const names = (span.call.tool_requests ?? []).map((r) => r.name).join(' ')
    return `${span.label} ${names} ${span.call.output_text?.text ?? ''}`.toLowerCase()
  }
  return `${span.label} ${JSON.stringify(span.call.args ?? {})} ${span.call.summary ?? ''}`.toLowerCase()
}

interface RowProps {
  span: Span
  depth: number
  total: number
  selected: boolean
  focusable: boolean
  expanded?: boolean
  childCount?: number
  onSelect: () => void
  onToggle?: () => void
}

function TreeRow({ span, depth, total, selected, focusable, expanded, childCount, onSelect, onToggle }: RowProps) {
  const meta = spanMeta(span)
  const Icon = meta.icon
  const failed = span.kind === 'tool' && span.call.failed
  const incomplete = Boolean(span.call.incomplete)
  const left = Math.min((span.start / total) * 100, 99)
  const width = Math.min(Math.max(1, (span.seconds / total) * 100), 100 - left)
  const tokens = span.kind === 'llm' && span.call.input != null && span.call.output != null ? span.call.input + span.call.output : null

  return (
    <li role="none" className="relative">
      {depth > 0 && <span className="absolute inset-y-0 left-[1.6rem] w-px bg-border" aria-hidden />}
      {selected && <span className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-primary" aria-hidden />}
      <div
        className={`flex items-center gap-1 py-1 pr-3 transition-colors ${selected ? 'bg-brand-soft/70' : 'hover:bg-muted/50'}`}
        style={{ paddingLeft: depth ? '2.25rem' : '0.5rem' }}
      >
        {onToggle ? (
          <button
            type="button"
            tabIndex={-1}
            onClick={onToggle}
            aria-label={expanded ? '收起子调用' : '展开子调用'}
            className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ChevronRight className={`size-3.5 transition-transform ${expanded ? 'rotate-90' : ''}`} aria-hidden />
          </button>
        ) : (
          depth === 0 && <span className="size-5 shrink-0" aria-hidden />
        )}
        <button
          type="button"
          role="treeitem"
          aria-selected={selected}
          aria-expanded={onToggle ? expanded : undefined}
          aria-level={depth + 1}
          tabIndex={focusable ? 0 : -1}
          data-span-key={span.key}
          onClick={onSelect}
          className={`${COLUMNS} min-w-0 flex-1 rounded py-0.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring`}
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className={`flex size-6 shrink-0 items-center justify-center rounded-md ${meta.chip}`}>
              <Icon className="size-3.5" aria-hidden />
            </span>
            <span
              className={`min-w-0 truncate text-[13px] ${span.kind === 'tool' ? 'font-mono' : 'font-medium'} ${
                failed ? 'text-red-700 dark:text-red-400' : 'text-foreground'
              }`}
            >
              {span.label}
            </span>
            {incomplete && (
              <span className="shrink-0 rounded bg-amber-100 px-1.5 text-xs font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
                未完成
              </span>
            )}
            {failed && (
              <span className="shrink-0 rounded bg-red-100 px-1.5 text-xs font-medium text-red-700 dark:bg-red-500/15 dark:text-red-300">
                失败
              </span>
            )}
            {tokens != null && (
              <span className="hidden shrink-0 text-xs tabular-nums text-muted-foreground sm:inline">{formatTokens(tokens)} tok</span>
            )}
            {!expanded && childCount ? <span className="shrink-0 text-xs text-muted-foreground">{childCount} 个工具</span> : null}
          </span>
          <span className="text-right text-xs tabular-nums text-muted-foreground">{spanDuration(span.seconds)}</span>
          <span className="relative h-2.5 rounded-sm bg-muted/60" aria-hidden>
            <span
              className={`absolute inset-y-0 rounded-sm ${meta.bar} ${incomplete ? 'opacity-40' : selected ? '' : 'opacity-85'}`}
              style={{ left: `${left}%`, width: `${width}%` }}
            />
          </span>
        </button>
      </div>
    </li>
  )
}

type Filter = 'all' | 'llm' | 'tool' | 'failed'

export function TraceTree({
  groups,
  total,
  selectedKey,
  onSelect,
}: {
  groups: SpanGroup[]
  total: number
  selectedKey: string | null
  onSelect: (span: Span) => void
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const listRef = useRef<HTMLUListElement>(null)

  const counts = useMemo(() => {
    const all = groups.flatMap((g) => [g.span, ...g.children])
    return {
      all: all.length,
      llm: all.filter((s) => s.kind === 'llm').length,
      tool: all.filter((s) => s.kind === 'tool').length,
      failed: all.filter((s) => s.kind === 'tool' && s.call.failed).length,
      subagent: all.filter((s) => s.kind === 'tool' && s.call.subagent).length,
    }
  }, [groups])

  const needle = query.trim().toLowerCase()
  const flat = filter !== 'all' || needle !== ''

  // 筛选或搜索时打平成列表，否则按父子结构展示
  const rows = useMemo(() => {
    if (flat) {
      const match = (s: Span) =>
        (filter === 'all' ||
          (filter === 'llm' ? s.kind === 'llm' : filter === 'tool' ? s.kind === 'tool' : s.kind === 'tool' && s.call.failed)) &&
        (!needle || searchText(s).includes(needle))
      return groups
        .flatMap((g) => [g.span, ...g.children])
        .filter(match)
        .map((span) => ({ span, depth: 0, group: null as SpanGroup | null }))
    }
    return groups.flatMap((g) => [
      { span: g.span, depth: 0, group: g.children.length ? g : null },
      ...(collapsed.has(g.span.key) ? [] : g.children.map((span) => ({ span, depth: 1, group: null }))),
    ])
  }, [groups, collapsed, filter, needle, flat])

  // 选中项可能来自详情面板里的跳转：被收起时先展开父节点，再滚到可见处
  const parentKey = useMemo(() => groups.find((g) => g.children.some((c) => c.key === selectedKey))?.span.key, [groups, selectedKey])
  const [revealed, setRevealed] = useState<string | null>(null)
  if (parentKey && selectedKey !== revealed && collapsed.has(parentKey)) {
    setRevealed(selectedKey)
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.delete(parentKey)
      return next
    })
  }
  useEffect(() => {
    if (!selectedKey) return
    listRef.current?.querySelector<HTMLElement>(`[data-span-key="${selectedKey}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [selectedKey, rows])

  const toggle = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const selectedVisible = rows.some((r) => r.span.key === selectedKey)
  const allCollapsed = groups.filter((g) => g.children.length).every((g) => collapsed.has(g.span.key))
  const hasChildren = groups.some((g) => g.children.length)

  const onKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    const index = rows.findIndex((r) => r.span.key === selectedKey)
    const move = (to: number) => {
      const row = rows[Math.max(0, Math.min(rows.length - 1, to))]
      if (!row) return
      e.preventDefault()
      onSelect(row.span)
      listRef.current?.querySelector<HTMLElement>(`[data-span-key="${row.span.key}"]`)?.focus()
    }
    if (e.key === 'ArrowDown') move(index + 1)
    else if (e.key === 'ArrowUp') move(index - 1)
    else if (e.key === 'Home') move(0)
    else if (e.key === 'End') move(rows.length - 1)
    else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && index >= 0) {
      const row = rows[index]
      if (!row.group) return
      const isCollapsed = collapsed.has(row.span.key)
      if ((e.key === 'ArrowLeft') !== isCollapsed) {
        e.preventDefault()
        toggle(row.span.key)
      }
    }
  }

  const filters: { key: Filter; label: string; count: number }[] = [
    { key: 'all', label: '全部', count: counts.all },
    { key: 'llm', label: '模型', count: counts.llm },
    { key: 'tool', label: '工具', count: counts.tool },
    { key: 'failed', label: '失败', count: counts.failed },
  ]

  const legend = [
    { label: '模型', className: 'bg-brand-500', show: counts.llm > 0 },
    { label: '工具', className: 'bg-amber-500', show: counts.tool > 0 },
    { label: '子智能体', className: 'bg-violet-500', show: counts.subagent > 0 },
    { label: '失败', className: 'bg-red-500', show: counts.failed > 0 },
  ].filter((l) => l.show)
  const ticks = axisTicks(total)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2.5 border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <div role="radiogroup" aria-label="筛选调用" className="inline-flex items-center rounded-lg bg-muted p-0.5">
            {filters.map((f) => (
              <button
                key={f.key}
                type="button"
                role="radio"
                aria-checked={filter === f.key}
                disabled={f.key !== 'all' && f.count === 0}
                onClick={() => setFilter(f.key)}
                className={`inline-flex h-7 items-center gap-1 rounded-md px-2 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40 ${
                  filter === f.key
                    ? 'bg-card font-medium text-foreground shadow-sm'
                    : f.key === 'failed' && f.count
                      ? 'text-red-600 dark:text-red-400'
                      : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {f.label}
                <span className="text-xs tabular-nums opacity-80">{f.count}</span>
              </button>
            ))}
          </div>
          <label className="relative min-w-32 flex-1">
            <span className="sr-only">搜索调用</span>
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape' && query) {
                  e.stopPropagation()
                  setQuery('')
                }
              }}
              placeholder="搜索工具名、参数或结果"
              className="h-8 w-full rounded-lg border border-input bg-background pl-7 pr-7 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-search-cancel-button]:hidden"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="清空搜索"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            )}
          </label>
          {!flat && hasChildren && (
            <button
              type="button"
              onClick={() =>
                setCollapsed(allCollapsed ? new Set() : new Set(groups.filter((g) => g.children.length).map((g) => g.span.key)))
              }
              className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {allCollapsed ? <ChevronsUpDown className="size-3.5" aria-hidden /> : <ChevronsDownUp className="size-3.5" aria-hidden />}
              {allCollapsed ? '全部展开' : '全部收起'}
            </button>
          )}
        </div>
        <ul className="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="图例">
          {legend.map((l) => (
            <li key={l.label} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={`h-2 w-3 rounded-sm ${l.className}`} aria-hidden />
              {l.label}
            </li>
          ))}
          <li className="ml-auto hidden items-center gap-1 text-xs text-muted-foreground sm:flex">
            <kbd className="rounded border border-border px-1 font-mono text-[10px]">↑↓</kbd>
            切换
            <kbd className="ml-1.5 rounded border border-border px-1 font-mono text-[10px]">←→</kbd>
            收起/展开
          </li>
        </ul>
      </div>
      <div className={`${COLUMNS} shrink-0 border-b border-border bg-muted/40 py-1.5 pl-8 pr-3 text-xs text-muted-foreground`} aria-hidden>
        <span>调用</span>
        <span className="text-right">耗时</span>
        <span className="relative h-4">
          {ticks.map((t, i) => {
            const pos = (t / total) * 100
            return (
              <span
                key={t}
                className={`absolute top-0 tabular-nums ${i === 0 ? '' : pos > 88 ? '-translate-x-full' : '-translate-x-1/2'}`}
                style={{ left: `${Math.min(pos, 100)}%` }}
              >
                {tickLabel(t)}
              </span>
            )
          })}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground">没有符合条件的调用</p>
        ) : (
          <ul ref={listRef} role="tree" aria-label="调用树" onKeyDown={onKeyDown} className="py-1">
            {rows.map((row, i) => (
              <TreeRow
                key={row.span.key}
                span={row.span}
                depth={row.depth}
                total={total}
                selected={row.span.key === selectedKey}
                focusable={row.span.key === selectedKey || (!selectedVisible && i === 0)}
                expanded={row.group ? !collapsed.has(row.span.key) : undefined}
                childCount={row.group?.children.length}
                onSelect={() => onSelect(row.span)}
                onToggle={row.group ? () => toggle(row.span.key) : undefined}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

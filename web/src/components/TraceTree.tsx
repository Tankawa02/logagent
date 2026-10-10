import { Bot, ChevronRight, Search, Wrench, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { formatDuration, formatTokens } from '../lib/format'
import type { Span } from './TraceSpanDetail'

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

function spanTone(span: Span) {
  if (span.kind === 'llm') return { bar: 'bg-brand-500', icon: 'text-brand-600 dark:text-brand-400' }
  if (span.call.failed) return { bar: 'bg-red-500', icon: 'text-red-600 dark:text-red-400' }
  if (span.call.subagent) return { bar: 'bg-violet-500', icon: 'text-violet-600 dark:text-violet-400' }
  return { bar: 'bg-amber-500', icon: 'text-amber-600 dark:text-amber-500' }
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
  const tone = spanTone(span)
  const Icon = span.kind === 'llm' ? Bot : Wrench
  const failed = span.kind === 'tool' && span.call.failed
  const incomplete = Boolean(span.call.incomplete)
  const left = Math.min((span.start / total) * 100, 99.4)
  const width = Math.max(0.8, (span.seconds / total) * 100)
  const tokens = span.kind === 'llm' && span.call.input != null && span.call.output != null ? span.call.input + span.call.output : null

  return (
    <li role="none" className="relative">
      {depth > 0 && <span className="absolute inset-y-0 left-[1.4rem] w-px bg-zinc-200 dark:bg-zinc-800" aria-hidden />}
      <div
        className={`group flex items-center gap-2 border-l-2 py-1.5 pr-3 ${
          selected ? 'border-brand-500 bg-brand-50 dark:bg-brand-950/40' : 'border-transparent hover:bg-zinc-50 dark:hover:bg-zinc-800/50'
        }`}
        style={{ paddingLeft: depth ? '2rem' : '0.5rem' }}
      >
        {onToggle ? (
          <button
            type="button"
            tabIndex={-1}
            onClick={onToggle}
            aria-label={expanded ? '收起子调用' : '展开子调用'}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-zinc-400 hover:bg-zinc-200 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
          >
            <ChevronRight className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-90' : ''}`} aria-hidden />
          </button>
        ) : (
          <span className="w-5 shrink-0" aria-hidden />
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
          className="flex min-w-0 flex-1 items-center gap-2 rounded text-left outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <Icon className={`h-3.5 w-3.5 shrink-0 ${tone.icon}`} aria-hidden />
          <span
            className={`min-w-0 truncate font-mono text-xs ${
              failed ? 'text-red-700 dark:text-red-400' : 'text-zinc-800 dark:text-zinc-200'
            } ${selected ? 'font-medium' : ''}`}
          >
            {span.label}
          </span>
          {incomplete && (
            <span className="shrink-0 rounded bg-amber-100 px-1 text-[10px] text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              未完成
            </span>
          )}
          {failed && (
            <span className="shrink-0 rounded bg-red-100 px-1 text-[10px] text-red-700 dark:bg-red-950 dark:text-red-300">失败</span>
          )}
          {!expanded && childCount ? <span className="shrink-0 text-[11px] text-zinc-400">{childCount} 个工具</span> : null}
          <span className="ml-auto flex shrink-0 items-center gap-2 pl-2">
            {tokens != null && (
              <span className="hidden font-mono text-[11px] tabular-nums text-zinc-400 sm:inline">{formatTokens(tokens)} tok</span>
            )}
            <span className="relative hidden h-1.5 w-20 rounded-full bg-zinc-100 lg:block xl:w-28 dark:bg-zinc-800" aria-hidden>
              <span
                className={`absolute inset-y-0 rounded-full ${tone.bar} ${incomplete ? 'opacity-40' : ''}`}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </span>
            <span className="w-12 text-right font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">
              {spanDuration(span.seconds)}
            </span>
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800">
        <div className="flex flex-wrap items-center gap-2">
          <div role="radiogroup" aria-label="筛选调用" className="inline-flex rounded-md bg-zinc-100 p-0.5 dark:bg-zinc-800">
            {filters.map((f) => (
              <button
                key={f.key}
                type="button"
                role="radio"
                aria-checked={filter === f.key}
                disabled={f.key !== 'all' && f.count === 0}
                onClick={() => setFilter(f.key)}
                className={`rounded px-2 py-0.5 text-xs tabular-nums disabled:cursor-not-allowed disabled:opacity-40 ${
                  filter === f.key
                    ? 'bg-white font-medium text-zinc-900 shadow-xs dark:bg-zinc-950 dark:text-zinc-100'
                    : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
              >
                {f.label}
                <span className={`ml-1 ${f.key === 'failed' && f.count ? 'text-red-600' : 'text-zinc-400'}`}>{f.count}</span>
              </button>
            ))}
          </div>
          {!flat && hasChildren && (
            <button
              type="button"
              onClick={() =>
                setCollapsed(allCollapsed ? new Set() : new Set(groups.filter((g) => g.children.length).map((g) => g.span.key)))
              }
              className="ml-auto rounded px-2 py-0.5 text-xs text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            >
              {allCollapsed ? '全部展开' : '全部收起'}
            </button>
          )}
        </div>
        <label className="relative block">
          <span className="sr-only">搜索调用</span>
          <Search className="pointer-events-none absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" aria-hidden />
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
            className="w-full rounded-md border border-zinc-200 bg-white py-1 pr-7 pl-7 text-xs text-zinc-800 outline-none placeholder:text-zinc-400 focus:border-brand-500 focus:ring-1 focus:ring-brand-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200 [&::-webkit-search-cancel-button]:hidden"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="清空搜索"
              className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-0.5 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          )}
        </label>
      </div>
      <div
        className="hidden items-center justify-end gap-2 px-3 pt-1.5 font-mono text-[10px] tabular-nums text-zinc-400 lg:flex"
        aria-hidden
      >
        <span className="flex w-20 justify-between xl:w-28">
          <span>0s</span>
          <span>{formatDuration(total)}</span>
        </span>
        <span className="w-12" />
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-zinc-400">没有匹配的调用</p>
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

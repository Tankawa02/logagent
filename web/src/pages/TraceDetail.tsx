import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ArrowRight, ChevronDown, ChevronRight, MessageSquare } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { TopBar } from '../components/TopBar'
import { Badge, buttonClass, Card, CardHeader, Empty, ErrorBox, ListSkeleton } from '../components/ui'
import { TraceStatusBadge } from '../components/TraceStatusBadge'
import { api } from '../lib/api'
import { baseName, formatDuration, formatGenerated, formatTokens, formatUsd, TURN_STATUS } from '../lib/format'
import type { ToolCall, TurnPayload } from '../lib/types'
import { TraceSpanDetail, type Span, type SpanLinks, type Tab } from '../components/TraceSpanDetail'
import { groupSpans, TraceTree } from '../components/TraceTree'

function buildSpans(payload: TurnPayload): { spans: Span[]; total: number; approximate: boolean } {
  const llm = payload.llm_calls ?? []
  const tools = payload.tool_calls ?? []
  const approximate = tools.some((t) => t.started == null)
  // 收尾 / 经验检索调用是事后追加进数组的，编号要按实际开始时间排，不能按数组下标
  const rank = new Map(
    llm
      .map((call, i) => ({ i, start: call.started ?? 0 }))
      .sort((a, b) => a.start - b.start || a.i - b.i)
      .map((entry, order) => [entry.i, order + 1]),
  )
  const spans: Span[] = llm.map((call, i) => ({
    kind: 'llm',
    key: `llm-${i}`,
    label: `模型调用 #${rank.get(i)}`,
    start: call.started,
    seconds: call.seconds,
    call,
    index: i,
  }))
  // 旧记录没有开始时间：按记录顺序首尾相接，只能看出相对长短
  let cursor = 0
  tools.forEach((call, i) => {
    const start = call.started ?? cursor
    cursor = start + call.seconds
    spans.push({ kind: 'tool', key: `tool-${i}`, label: call.name, start, seconds: call.seconds, call })
  })
  spans.sort((a, b) => a.start - b.start || (a.kind === 'llm' ? -1 : 1))
  const end = Math.max(0, ...spans.map((s) => s.start + s.seconds))
  return { spans, total: Math.max(payload.elapsed_seconds || 0, end, 0.001), approximate }
}

function QuestionTitle({ text }: { text: string }) {
  const ref = useRef<HTMLHeadingElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      if (!expanded) setOverflowing(el.scrollHeight > el.clientHeight + 1)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [text, expanded])
  return (
    <div className="space-y-1">
      <h1
        ref={ref}
        title={expanded ? undefined : text}
        className={`whitespace-pre-wrap break-words text-pretty text-[15px] font-medium leading-relaxed text-foreground ${expanded ? '' : 'line-clamp-2'}`}
      >
        {text}
      </h1>
      {(overflowing || expanded) && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDown className={`size-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`} aria-hidden />
          {expanded ? '收起' : '展开全文'}
        </button>
      )}
    </div>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: ReactNode }) {
  return (
    <div className="min-w-0 bg-card px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate font-heading text-xl font-semibold tabular-nums tracking-tight text-foreground">{value}</dd>
      {hint && <dd className="mt-0.5 truncate text-xs text-muted-foreground">{hint}</dd>}
    </div>
  )
}

function ToolSummary({ tools }: { tools: ToolCall[] }) {
  const rows = useMemo(() => {
    const map = new Map<string, { count: number; seconds: number; failed: number }>()
    for (const t of tools) {
      const row = map.get(t.name) ?? { count: 0, seconds: 0, failed: 0 }
      row.count += 1
      row.seconds += t.seconds
      row.failed += t.failed ? 1 : 0
      map.set(t.name, row)
    }
    return [...map.entries()].sort((a, b) => b[1].seconds - a[1].seconds)
  }, [tools])
  if (!rows.length) return null
  return (
    <Card>
      <CardHeader title="工具汇总" />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-zinc-500">
              <th className="px-4 py-2 font-medium">工具</th>
              <th className="px-4 py-2 text-right font-medium">次数</th>
              <th className="px-4 py-2 text-right font-medium">总耗时</th>
              <th className="px-4 py-2 text-right font-medium">平均</th>
              <th className="px-4 py-2 text-right font-medium">失败</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {rows.map(([name, row]) => (
              <tr key={name} className="tabular-nums">
                <td className="px-4 py-2 font-mono text-xs">{name}</td>
                <td className="px-4 py-2 text-right font-mono text-xs">{row.count}</td>
                <td className="px-4 py-2 text-right font-mono text-xs">{formatDuration(row.seconds)}</td>
                <td className="px-4 py-2 text-right font-mono text-xs">{formatDuration(row.seconds / row.count)}</td>
                <td className={`px-4 py-2 text-right font-mono text-xs ${row.failed ? 'text-red-600' : 'text-zinc-400'}`}>{row.failed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

const SETTING_LABELS: Record<string, string> = {
  since: '开始时间',
  until: '结束时间',
  timezone: '时区',
  baseline: '基线',
  encoding: '编码',
  budget: 'tokens 预算',
  max_steps: '最大步数',
}

export function TraceDetail({
  name,
  turn,
  spanKey,
  onSpanChange,
}: {
  name: string
  turn: number
  spanKey: string | null
  onSpanChange: (key: string) => void
}) {
  const payload = useQuery({ queryKey: ['owner', name, 'turn', turn], queryFn: () => api.turn({ kind: 'owner', name }, turn) })
  const view = useMemo(() => (payload.data ? buildSpans(payload.data) : null), [payload.data])
  const groups = useMemo(() => (view ? groupSpans(view.spans) : []), [view])
  const ordered = useMemo(() => groups.flatMap((g) => [g.span, ...g.children]), [groups])
  const [tab, setTab] = useState<Tab>('output')
  const selectedIndex = Math.max(
    0,
    ordered.findIndex((s) => s.key === spanKey),
  )
  const selected = ordered[selectedIndex] ?? null
  const links = useMemo<SpanLinks>(() => {
    const toolByRequestId = new Map<string, Span>()
    for (const s of ordered) if (s.kind === 'tool' && s.call.call_id) toolByRequestId.set(s.call.call_id, s)
    const parent = groups.find((g) => g.children.some((c) => c.key === selected?.key))?.span ?? null
    return { toolByRequestId, parent, onJump: onSpanChange }
  }, [ordered, groups, selected?.key, onSpanChange])
  const detailRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    detailRef.current?.scrollTo({ top: 0 })
  }, [selected?.key])
  const firstFailed = ordered.find((s) => s.kind === 'tool' && s.call.failed)

  if (payload.isLoading) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
        <ListSkeleton rows={8} label="加载执行记录" />
      </div>
    )
  }
  if (payload.error || !payload.data || !view) {
    return (
      <div className="p-6">
        <ErrorBox error={payload.error ?? '加载失败'} />
      </div>
    )
  }

  const data = payload.data
  const status = TURN_STATUS[data.status] ?? TURN_STATUS.ok
  const tools = data.tool_calls ?? []
  const llm = data.llm_calls ?? []
  const failed = tools.filter((t) => t.failed).length
  const toolSeconds = tools.reduce((sum, t) => sum + t.seconds, 0)
  const llmSeconds = llm.reduce((sum, c) => sum + c.seconds, 0)
  const firstTokens = llm.flatMap((c) => (c.first_token != null ? [c.first_token] : []))
  const avgFirstToken = firstTokens.reduce((sum, s) => sum + s, 0) / Math.max(1, firstTokens.length)
  const cost = data.cost?.usd != null ? data.cost : null
  const settings = Object.entries(data.settings ?? {}).filter(([key, value]) => key in SETTING_LABELS && value !== null && value !== '')
  const legacy = data.provenance === 'legacy_unknown'

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar
        actions={
          <Link to="/sessions/$name" params={{ name }} search={{ turn }} className={buttonClass('secondary', 'sm')}>
            <MessageSquare className="size-3.5" aria-hidden />
            <span className="hidden sm:inline">打开对话</span>
            <span className="sr-only sm:hidden">打开对话</span>
          </Link>
        }
      >
        <nav aria-label="面包屑" className="flex min-w-0 items-center gap-1.5 text-sm">
          <Link to="/trace" className="shrink-0 text-muted-foreground transition-colors hover:text-foreground">
            执行记录
          </Link>
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <Link
            to="/trace"
            search={{ session: name }}
            title={`只看会话 ${name}`}
            className="min-w-0 truncate font-medium text-foreground transition-colors hover:underline"
          >
            {name} · 第 {turn} 轮
          </Link>
        </nav>
      </TopBar>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl space-y-5 px-4 py-6 sm:px-6">
          <header className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <TraceStatusBadge status={data.status} />
              <span className="rounded-md bg-muted px-1.5 py-0.5 text-foreground/80">第 {turn} 轮</span>
              {data.budget_hit && <Badge tone="amber">触及 tokens 预算</Badge>}
              <span className="font-mono">{data.model || '未知模型'}</span>
              <span aria-hidden>·</span>
              <span className="tabular-nums">{formatGenerated(data.generated_at)}</span>
            </div>
            <QuestionTitle text={data.question || '（无问题文本）'} />
            <span className="sr-only">状态：{status.label}</span>
          </header>

          {data.error && <ErrorBox error={data.error} />}
          {legacy && <p className="text-sm text-muted-foreground">这是旧版会话恢复出来的记录，没有保存逐轮的耗时与工具调用。</p>}

          <dl
            className={`grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3 ${cost ? 'lg:grid-cols-5' : 'lg:grid-cols-4'}`}
          >
            <Metric
              label="总耗时"
              value={formatDuration(data.elapsed_seconds)}
              hint={
                llm.length
                  ? `模型占 ${formatDuration(llmSeconds)} · 工具 ${formatDuration(toolSeconds)}`
                  : `工具 ${formatDuration(toolSeconds)}`
              }
            />
            <Metric
              label="模型调用"
              value={llm.length ? String(llm.length) : '—'}
              hint={llm.length ? (firstTokens.length ? `首 token 平均 ${avgFirstToken.toFixed(1)}s` : '主代理') : '旧记录未保存'}
            />
            <Metric
              label="Token"
              value={formatTokens(data.usage?.total)}
              hint={`输入 ${formatTokens(data.usage?.input)} · 输出 ${formatTokens(data.usage?.output)}${
                data.usage?.cache_read ? ` · 缓存 ${formatTokens(data.usage.cache_read)}` : ''
              }`}
            />
            <Metric
              label="工具调用"
              value={String(tools.length)}
              hint={
                failed && firstFailed ? (
                  <button
                    type="button"
                    onClick={() => {
                      onSpanChange(firstFailed.key)
                      setTab('output')
                      document.getElementById('trace-spans')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                    className="inline-flex items-center gap-1 rounded-sm text-red-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:text-red-400"
                  >
                    {failed} 次失败，查看
                    <ArrowRight className="size-3" aria-hidden />
                  </button>
                ) : (
                  '全部成功'
                )
              }
            />
            {cost && (
              <Metric
                label="费用"
                value={`${formatUsd(cost.usd)}${cost.complete ? '' : '+'}`}
                hint={cost.complete ? '按模型公开价估算' : '部分模型未定价，实际更高'}
              />
            )}
          </dl>

          <div id="trace-spans" className="scroll-mt-4">
            <Card className="overflow-hidden">
              <CardHeader title="执行过程">
                {view.spans.length > 0 && (
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {llm.length} 次模型调用 · {tools.length} 次工具调用
                  </span>
                )}
              </CardHeader>
              {view.approximate && view.spans.length > 0 && (
                <p className="border-b border-zinc-100 px-4 py-2 text-xs text-zinc-500 dark:border-zinc-800">
                  这一轮由旧版本记录，没有保存开始时间，时间轴按调用顺序首尾相接排列，仅供比较长短。
                </p>
              )}
              {view.spans.length === 0 ? (
                <Empty>这一轮没有模型或工具调用记录</Empty>
              ) : (
                <div className="grid md:h-[min(80vh,52rem)] md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] md:grid-rows-[minmax(0,1fr)]">
                  {/* 固定高度 + 单行 minmax(0,1fr)：左右两栏各自在内部滚动。之前左栏 overflow-visible，
                    全部展开后内容超出被卡片 overflow-hidden 裁掉，滚动定位选中项时还会把整块内容顶上去 */}
                  <div className="flex h-[45vh] min-h-0 min-w-0 flex-col border-b border-zinc-100 md:h-auto md:border-r md:border-b-0 dark:border-zinc-800">
                    <TraceTree
                      groups={groups}
                      total={view.total}
                      selectedKey={selected?.key ?? null}
                      onSelect={(s) => onSpanChange(s.key)}
                    />
                  </div>
                  <div ref={detailRef} className="min-h-0 min-w-0 bg-muted/30 md:overflow-auto">
                    <div>
                      {selected && (
                        <TraceSpanDetail
                          span={selected}
                          tab={tab}
                          onTabChange={setTab}
                          links={links}
                          nav={{
                            position: selectedIndex + 1,
                            count: ordered.length,
                            onPrev: selectedIndex > 0 ? () => onSpanChange(ordered[selectedIndex - 1].key) : undefined,
                            onNext: selectedIndex < ordered.length - 1 ? () => onSpanChange(ordered[selectedIndex + 1].key) : undefined,
                          }}
                        />
                      )}
                    </div>
                  </div>
                </div>
              )}
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <ToolSummary tools={tools} />
            <Card>
              <CardHeader title="输入与设置" />
              <dl className="space-y-2 px-4 py-3 text-sm">
                <div>
                  <dt className="text-xs text-zinc-500">日志</dt>
                  {data.logs.length ? (
                    data.logs.map((p) => (
                      <dd key={p} className="truncate font-mono text-xs text-zinc-700 dark:text-zinc-300" title={p}>
                        {baseName(p)} <span className="text-zinc-400">{p}</span>
                      </dd>
                    ))
                  ) : (
                    <dd className="text-xs text-zinc-400">无</dd>
                  )}
                </div>
                <div>
                  <dt className="text-xs text-zinc-500">源码</dt>
                  {data.code.length ? (
                    data.code.map((p) => (
                      <dd key={p} className="truncate font-mono text-xs text-zinc-700 dark:text-zinc-300" title={p}>
                        {p}
                      </dd>
                    ))
                  ) : (
                    <dd className="text-xs text-zinc-400">未提供</dd>
                  )}
                </div>
                {settings.map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-3">
                    <dt className="text-xs text-zinc-500">{SETTING_LABELS[key]}</dt>
                    <dd className="truncate font-mono text-xs text-zinc-700 dark:text-zinc-300">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          </div>
        </div>
      </div>
    </div>
  )
}

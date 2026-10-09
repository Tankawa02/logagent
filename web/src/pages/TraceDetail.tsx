import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ArrowLeft, Bot, ChevronRight, MessageSquare, Wrench } from 'lucide-react'
import { useMemo } from 'react'
import { Badge, Card, CardHeader, Empty, ErrorBox, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { baseName, formatDuration, formatGenerated, formatTokens, TURN_STATUS } from '../lib/format'
import type { LlmCall, ToolCall, TurnPayload } from '../lib/types'

type Span =
  | { kind: 'llm'; key: string; label: string; start: number; seconds: number; call: LlmCall }
  | { kind: 'tool'; key: string; label: string; start: number; seconds: number; call: ToolCall }

function buildSpans(payload: TurnPayload): { spans: Span[]; total: number; approximate: boolean } {
  const llm = payload.llm_calls ?? []
  const tools = payload.tool_calls ?? []
  const approximate = tools.some((t) => t.started == null)
  const spans: Span[] = llm.map((call, i) => ({
    kind: 'llm',
    key: `llm-${i}`,
    label: `模型调用 #${i + 1}`,
    start: call.started,
    seconds: call.seconds,
    call,
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

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <dt className="text-xs text-zinc-500">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-lg font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">{value}</dd>
      {hint && <dd className="truncate text-xs text-zinc-400">{hint}</dd>}
    </div>
  )
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-64 overflow-auto rounded-md bg-zinc-50 p-2 font-mono text-xs leading-relaxed text-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function SpanRow({ span, total }: { span: Span; total: number }) {
  const left = (span.start / total) * 100
  const width = Math.max(0.6, (span.seconds / total) * 100)
  const failed = span.kind === 'tool' && span.call.failed
  const sub = span.kind === 'tool' && span.call.subagent
  const incomplete = Boolean(span.call.incomplete)
  const base = span.kind === 'llm' ? 'bg-brand-500' : failed ? 'bg-red-500' : sub ? 'bg-violet-500' : 'bg-amber-500'
  // 未完成的段只量到本轮结束：用半透明 + 虚线边，和真实耗时区分开
  const color = incomplete ? `${base} opacity-40 outline-1 outline-dashed outline-zinc-500` : base
  const Icon = span.kind === 'llm' ? Bot : Wrench

  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 [&::-webkit-details-marker]:hidden">
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform group-open:rotate-90" aria-hidden />
          <div className="flex w-32 shrink-0 items-center gap-1.5 sm:w-44">
            <Icon className={`h-3.5 w-3.5 shrink-0 ${span.kind === 'llm' ? 'text-brand-600' : failed ? 'text-red-600' : 'text-amber-600'}`} aria-hidden />
            <span className={`truncate font-mono text-xs ${failed ? 'text-red-700 dark:text-red-400' : 'text-zinc-800 dark:text-zinc-200'}`} title={span.label}>
              {span.label}
            </span>
            {incomplete && <Badge tone="amber">未完成</Badge>}
          </div>
          <div className="relative h-4 min-w-0 flex-1 rounded bg-zinc-100 dark:bg-zinc-800" aria-hidden>
            <div className={`absolute inset-y-0.5 rounded-sm ${color}`} style={{ left: `${Math.min(left, 99.4)}%`, width: `${width}%` }} />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
            {formatDuration(span.seconds)}
          </span>
        </summary>
        <div className="space-y-2 border-t border-zinc-100 bg-zinc-50/60 px-4 py-3 pl-11 text-xs dark:border-zinc-800 dark:bg-zinc-900/60">
          <div className="flex flex-wrap gap-x-4 gap-y-1 tabular-nums text-zinc-500">
            <span>开始 +{formatDuration(span.start)}</span>
            <span>
              耗时 {span.seconds.toFixed(3)}s{incomplete && '（量到本轮结束，未完成）'}
            </span>
            {span.kind === 'llm' && (
              <>
                {span.call.first_token != null && <span>首字 {span.call.first_token.toFixed(2)}s</span>}
                {span.call.input == null || span.call.output == null ? (
                  <span>输出中断，用量未知</span>
                ) : (
                  <>
                    <span>输入 {span.call.input.toLocaleString()} tokens</span>
                    <span>输出 {span.call.output.toLocaleString()} tokens</span>
                  </>
                )}
                {span.call.tool_calls != null && <span>发起工具 {span.call.tool_calls} 个</span>}
                {span.call.model && <span className="font-mono">{span.call.model}</span>}
                {span.call.finish_reason && <span>结束原因 {span.call.finish_reason}</span>}
              </>
            )}
            {span.kind === 'tool' && span.call.subagent && <span>子代理 {span.call.subagent}</span>}
          </div>
          {span.kind === 'tool' && (
            <>
              {span.call.note && <p className="text-zinc-600 dark:text-zinc-400">旁白：{span.call.note}</p>}
              <div>
                <div className="mb-1 font-medium text-zinc-600 dark:text-zinc-300">参数</div>
                <JsonBlock value={span.call.args} />
              </div>
              <div>
                <div className="mb-1 font-medium text-zinc-600 dark:text-zinc-300">{span.call.failed ? '错误' : '结果摘要'}</div>
                <p className={`whitespace-pre-wrap break-words font-mono ${span.call.failed ? 'text-red-700 dark:text-red-400' : 'text-zinc-700 dark:text-zinc-300'}`}>
                  {span.call.summary || '（无）'}
                </p>
              </div>
            </>
          )}
        </div>
      </details>
    </li>
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

export function TraceDetail({ name, turn }: { name: string; turn: number }) {
  const payload = useQuery({ queryKey: ['owner', name, 'turn', turn], queryFn: () => api.turn({ kind: 'owner', name }, turn) })
  const view = useMemo(() => (payload.data ? buildSpans(payload.data) : null), [payload.data])

  if (payload.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
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
  const settings = Object.entries(data.settings ?? {}).filter(([key, value]) => key in SETTING_LABELS && value !== null && value !== '')
  const legacy = data.provenance === 'legacy_unknown'

  return (
    <main className="h-full overflow-auto">
      <div className="mx-auto max-w-5xl space-y-5 px-4 py-6 sm:px-6">
        <nav className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <Link to="/trace" className="inline-flex items-center gap-1 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            全部 Trace
          </Link>
          <span className="text-zinc-300" aria-hidden>
            /
          </span>
          <Link to="/trace" search={{ session: name }} className="font-mono text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
            {name}
          </Link>
          <Link
            to="/sessions/$name"
            params={{ name }}
            search={{ turn }}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <MessageSquare className="h-3.5 w-3.5" aria-hidden />
            打开对话
          </Link>
        </nav>

        <header className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={status.tone}>{status.label}</Badge>
            <Badge tone="gray">第 {turn} 轮</Badge>
            {data.budget_hit && <Badge tone="amber">触及 tokens 预算</Badge>}
            <span className="text-xs tabular-nums text-zinc-500">{formatGenerated(data.generated_at)}</span>
          </div>
          <h1 className="text-lg font-semibold leading-snug text-zinc-900 text-pretty dark:text-zinc-50">{data.question || '（无问题文本）'}</h1>
          <p className="font-mono text-xs text-zinc-500">{data.model || '未知模型'}</p>
        </header>

        {data.error && <ErrorBox error={data.error} />}
        {legacy && <p className="text-sm text-zinc-500">这是旧版会话恢复出来的记录，没有保存逐轮的耗时与工具调用。</p>}

        <Card>
          <dl className="grid grid-cols-2 divide-x divide-y divide-zinc-100 lg:grid-cols-4 lg:divide-y-0 dark:divide-zinc-800">
            <Metric label="总耗时" value={formatDuration(data.elapsed_seconds)} hint={llm.length ? `模型 ${formatDuration(llmSeconds)} · 工具 ${formatDuration(toolSeconds)}` : `工具 ${formatDuration(toolSeconds)}`} />
            <Metric label="模型调用" value={llm.length ? String(llm.length) : '—'} hint={llm.length ? '主代理' : '旧记录未保存'} />
            <Metric
              label="tokens"
              value={formatTokens(data.usage?.total)}
              hint={`输入 ${formatTokens(data.usage?.input)} · 输出 ${formatTokens(data.usage?.output)}`}
            />
            <Metric label="工具调用" value={String(tools.length)} hint={failed ? `${failed} 次失败` : '无失败'} />
          </dl>
        </Card>

        <Card>
          <CardHeader title="执行过程">
            <span className="flex items-center gap-3 text-xs text-zinc-500">
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-sm bg-brand-500" aria-hidden />
                模型
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-sm bg-amber-500" aria-hidden />
                工具
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-sm bg-violet-500" aria-hidden />
                子代理
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-sm bg-red-500" aria-hidden />
                失败
              </span>
            </span>
          </CardHeader>
          {view.approximate && view.spans.length > 0 && (
            <p className="border-b border-zinc-100 px-4 py-2 text-xs text-zinc-500 dark:border-zinc-800">
              这一轮由旧版本记录，没有保存开始时间，下图按调用顺序首尾相接排列，仅供比较长短。
            </p>
          )}
          {view.spans.length === 0 ? (
            <Empty>这一轮没有模型或工具调用记录</Empty>
          ) : (
            <>
              <div className="flex items-center gap-3 px-4 pt-2 text-[11px] tabular-nums text-zinc-400" aria-hidden>
                <span className="w-3.5 shrink-0" />
                <span className="w-32 shrink-0 sm:w-44" />
                <span className="flex flex-1 justify-between">
                  <span>0s</span>
                  <span>{formatDuration(view.total / 2)}</span>
                  <span>{formatDuration(view.total)}</span>
                </span>
                <span className="w-14 shrink-0" />
              </div>
              <ul className="divide-y divide-zinc-100 pb-1 dark:divide-zinc-800">
                {view.spans.map((span) => (
                  <SpanRow key={span.key} span={span} total={view.total} />
                ))}
              </ul>
            </>
          )}
        </Card>

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
    </main>
  )
}

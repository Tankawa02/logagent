import { ArrowUpRight, Bot, Brain, Check, ChevronLeft, ChevronRight, Copy, CornerLeftUp, Users, Wrench } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { formatDuration } from '../lib/format'
import { useCopy } from '../lib/hooks'
import type { ClippedText, LlmCall, TraceMessage, ToolCall, ToolRequest } from '../lib/types'

export type Span =
  | { kind: 'llm'; key: string; label: string; start: number; seconds: number; call: LlmCall; index: number }
  | { kind: 'tool'; key: string; label: string; start: number; seconds: number; call: ToolCall }

export type Tab = 'input' | 'output'

/** 树和详情共用的配色：方形图标底色、时间条颜色、类型名 */
export function spanMeta(span: Span) {
  if (span.kind === 'llm') return { icon: Bot, kindLabel: '模型调用', chip: 'bg-brand-soft text-brand-strong', bar: 'bg-brand-500' }
  if (span.call.failed)
    return {
      icon: Wrench,
      kindLabel: span.call.subagent ? '子智能体' : '工具调用',
      chip: 'bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300',
      bar: 'bg-red-500',
    }
  if (span.call.subagent)
    return {
      icon: Users,
      kindLabel: '子智能体',
      chip: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300',
      bar: 'bg-violet-500',
    }
  return {
    icon: Wrench,
    kindLabel: '工具调用',
    chip: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
    bar: 'bg-amber-500',
  }
}

/** 详情面板里的跳转：模型调用 -> 它发起的工具，工具 -> 发起它的模型调用 */
export interface SpanLinks {
  toolByRequestId: Map<string, Span>
  parent: Span | null
  onJump: (key: string) => void
}

const ROLE_STYLE: Record<string, { label: string; className: string }> = {
  system: { label: '系统', className: 'bg-muted text-muted-foreground' },
  user: { label: '用户', className: 'bg-brand-soft text-brand-strong' },
  assistant: { label: '助手', className: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300' },
  tool: { label: '工具结果', className: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300' },
}

function CopyButton({ text }: { text: string }) {
  const { state, copy } = useCopy()
  return (
    <button
      type="button"
      onClick={() => void copy(text)}
      aria-label={state === 'copied' ? '已复制' : '复制'}
      title={state === 'failed' ? '复制失败' : '复制'}
      className="absolute top-1.5 right-1.5 rounded bg-white/90 p-1 text-zinc-400 opacity-0 ring-1 ring-zinc-200 transition-opacity group-hover/code:opacity-100 hover:text-zinc-700 focus-visible:opacity-100 dark:bg-zinc-900/90 dark:ring-zinc-700 dark:hover:text-zinc-200"
    >
      {state === 'copied' ? <Check className="h-3 w-3 text-brand-600" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
    </button>
  )
}

function JsonBlock({ value }: { value: unknown }) {
  const text = JSON.stringify(value, null, 2)
  return (
    <div className="group/code relative">
      <CopyButton text={text} />
      <pre className="max-h-72 overflow-auto rounded-md bg-white p-2.5 font-mono text-xs leading-relaxed text-zinc-700 ring-1 ring-zinc-200 dark:bg-zinc-950 dark:text-zinc-300 dark:ring-zinc-800">
        {text}
      </pre>
    </div>
  )
}

function TextBlock({ text, chars, tone = 'normal' }: { text: string; chars?: number; tone?: 'normal' | 'error' | 'muted' }) {
  const truncated = chars != null && chars > text.length
  const color =
    tone === 'error'
      ? 'text-red-700 dark:text-red-400'
      : tone === 'muted'
        ? 'text-zinc-500 dark:text-zinc-400'
        : 'text-zinc-800 dark:text-zinc-200'
  return (
    <div className="group/code relative">
      {text && <CopyButton text={text} />}
      <pre
        className={`max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-white p-2.5 font-mono text-xs leading-relaxed ring-1 ring-zinc-200 dark:bg-zinc-950 dark:ring-zinc-800 ${color}`}
      >
        {text || '（空）'}
      </pre>
      {truncated && (
        <p className="mt-1 text-xs text-zinc-400">
          仅保存前 {text.length.toLocaleString()} 字，原文共 {chars.toLocaleString()} 字
        </p>
      )}
    </div>
  )
}

function Section({ title, aside, children }: { title: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-medium text-zinc-600 dark:text-zinc-300">{title}</h4>
        {aside && <span className="text-xs text-zinc-400">{aside}</span>}
      </div>
      {children}
    </section>
  )
}

function ToolRequestList({ requests, links }: { requests: ToolRequest[]; links?: SpanLinks }) {
  return (
    <ul className="space-y-2">
      {requests.map((req, i) => {
        const target = req.id ? links?.toolByRequestId.get(req.id) : undefined
        return (
          <li key={req.id || i} className="space-y-1">
            <div className="flex items-center gap-1.5 font-mono text-xs text-zinc-700 dark:text-zinc-300">
              <Wrench className="h-3 w-3 text-amber-600" aria-hidden />
              {req.name}
              {target && links && (
                <button
                  type="button"
                  onClick={() => links.onJump(target.key)}
                  className={`ml-auto inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 font-sans text-[11px] hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                    target.kind === 'tool' && target.call.failed ? 'text-red-600' : 'text-brand-700 dark:text-brand-400'
                  }`}
                >
                  {target.kind === 'tool' && target.call.failed ? '查看结果（失败）' : '查看结果'}
                  <ArrowUpRight className="h-3 w-3" aria-hidden />
                </button>
              )}
            </div>
            <JsonBlock value={req.args} />
          </li>
        )
      })}
    </ul>
  )
}

function MessageItem({ message }: { message: TraceMessage }) {
  const role = ROLE_STYLE[message.role] ?? { label: message.role, className: 'bg-muted text-muted-foreground' }
  // 系统提示和很长的工具结果默认收起，先看清整体顺序
  const [open, setOpen] = useState(message.role !== 'system' && message.chars <= 1200)
  const preview =
    message.text.split('\n').find((line) => line.trim()) ||
    (message.tool_requests?.length ? `调用 ${message.tool_requests.map((r) => r.name).join('、')}` : '')
  return (
    <li className="overflow-hidden rounded-lg border border-border bg-card">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <ChevronRight className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden />
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${role.className}`}>{role.label}</span>
        {message.name && <span className="shrink-0 truncate font-mono text-xs text-foreground/80">{message.name}</span>}
        {!open && <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground">{preview}</span>}
        <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">{message.chars.toLocaleString()} 字符</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border p-2.5">
          {(message.text || !message.tool_requests?.length) && <TextBlock text={message.text} chars={message.chars} />}
          {message.tool_requests?.length ? <ToolRequestList requests={message.tool_requests} /> : null}
        </div>
      )}
    </li>
  )
}

function LlmInput({ call, index }: { call: LlmCall; index: number }) {
  if (!call.input_messages) return <p className="text-zinc-500">这一轮由旧版本记录，没有保存模型输入。</p>
  const hint = call.input_full
    ? index === 0
      ? '完整输入'
      : '上下文被压缩过，以下为这次调用的完整输入'
    : `上下文共 ${call.input_count ?? '?'} 条消息，这里只列出相对上一次调用新增的部分（上一次的模型输出见上一个模型调用）`
  return (
    <Section title={`消息（${call.input_messages.length}）`} aside={call.input_count != null ? `上下文 ${call.input_count} 条` : undefined}>
      <p className="text-xs text-zinc-500">{hint}</p>
      {call.input_messages.length ? (
        <ol className="space-y-1.5">
          {call.input_messages.map((m, i) => (
            <MessageItem key={i} message={m} />
          ))}
        </ol>
      ) : (
        <p className="text-zinc-400">无新增消息</p>
      )}
    </Section>
  )
}

function clippedText(value: unknown): ClippedText | undefined {
  if (!value || typeof value !== 'object') return undefined
  const { text, chars } = value as Partial<ClippedText>
  if (typeof text !== 'string') return undefined
  return { text, chars: typeof chars === 'number' ? chars : text.length }
}

function LlmOutput({ call, links }: { call: LlmCall; links?: SpanLinks }) {
  const hasText = Boolean(call.output_text?.text)
  // 旧版本把��考 token 数（数字）写进了同名的 reasoning 字段，覆盖了思考原文
  const reasoning = clippedText(call.reasoning)
  if (!call.output_text && !reasoning && !call.tool_requests) {
    return <p className="text-zinc-500">这一轮由旧版本记录，没有保存模型输出。</p>
  }
  return (
    <div className="space-y-3">
      {reasoning && (
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 [&::-webkit-details-marker]:hidden">
            <Brain className="h-3.5 w-3.5 text-violet-500" aria-hidden />
            思考过程
            <span className="font-normal text-zinc-400">{reasoning.chars.toLocaleString()} 字 · 点击展开</span>
          </summary>
          <div className="mt-1.5">
            <TextBlock text={reasoning.text} chars={reasoning.chars} tone="muted" />
          </div>
        </details>
      )}
      {(hasText || !call.tool_requests?.length) && (
        <Section title={call.incomplete ? '回复正文（中途断开，仅到断开处）' : '回复正文'}>
          <TextBlock text={call.output_text?.text ?? ''} chars={call.output_text?.chars} />
        </Section>
      )}
      {call.tool_requests?.length ? (
        <Section title={`发起工具调用（${call.tool_requests.length}）`}>
          <ToolRequestList requests={call.tool_requests} links={links} />
        </Section>
      ) : null}
    </div>
  )
}

function ToolInput({ call }: { call: ToolCall }) {
  return (
    <div className="space-y-3">
      {call.note && (
        <Section title="调用前的旁白">
          <p className="text-zinc-700 dark:text-zinc-300">{call.note}</p>
        </Section>
      )}
      <Section title="参数">
        <JsonBlock value={call.args} />
      </Section>
    </div>
  )
}

function ToolOutput({ call }: { call: ToolCall }) {
  const hasRaw = call.output_chars != null
  return (
    <div className="space-y-3">
      <Section title={call.failed ? '错误' : '结果摘要'}>
        <p
          className={`whitespace-pre-wrap break-words ${call.failed ? 'text-red-700 dark:text-red-400' : 'text-zinc-700 dark:text-zinc-300'}`}
        >
          {call.summary || '（无）'}
        </p>
      </Section>
      {hasRaw ? (
        <Section title="返回给模型的原文" aside={`${(call.output_chars ?? 0).toLocaleString()} 字`}>
          <TextBlock text={call.output ?? ''} chars={call.output_chars ?? undefined} tone={call.failed ? 'error' : 'normal'} />
        </Section>
      ) : (
        <p className="text-zinc-500">这一轮由旧版本记录，只保存了结果摘要。</p>
      )}
    </div>
  )
}

function Metadata({ span }: { span: Span }) {
  const incomplete = Boolean(span.call.incomplete)
  const items: { label: string; value: string; title?: string }[] = [
    { label: '开始于', value: `+${formatDuration(span.start)}` },
    {
      label: '耗时',
      value: `${span.seconds.toFixed(span.seconds < 10 ? 2 : 1)}s${incomplete ? '（未完成）' : ''}`,
      title: incomplete ? '量到本轮结束，调用本身没有完成' : undefined,
    },
  ]
  if (span.kind === 'llm') {
    const { call } = span
    items.push({ label: '首 token', value: call.first_token != null ? `${call.first_token.toFixed(2)}s` : '—' })
    items.push({ label: '输入 token', value: call.input != null ? call.input.toLocaleString() : '未知' })
    items.push({
      label: '输出 token',
      value:
        call.output != null
          ? `${call.output.toLocaleString()}${call.reasoning_tokens ? `（推理 ${call.reasoning_tokens.toLocaleString()}）` : ''}`
          : '未知',
    })
    items.push({ label: '结束原因', value: call.finish_reason || (incomplete ? '中途断开' : '—') })
    if (call.model) items.push({ label: '模型', value: call.model, title: call.model })
  } else {
    const { call } = span
    if (call.call_id) items.push({ label: '调用 ID', value: call.call_id, title: call.call_id })
    if (call.output_chars != null) items.push({ label: '返回字符', value: call.output_chars.toLocaleString() })
    if (call.subagent) items.push({ label: '子智能体', value: call.subagent })
  }
  return (
    <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
      {items.map((m) => (
        <div key={m.label} className="min-w-0 bg-card px-3 py-2">
          <dt className="text-xs text-muted-foreground">{m.label}</dt>
          <dd className="mt-0.5 truncate font-mono text-[13px] tabular-nums text-foreground" title={m.title}>
            {m.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

interface NavProps {
  position: number
  count: number
  onPrev?: () => void
  onNext?: () => void
}

export function TraceSpanDetail({
  span,
  tab,
  onTabChange,
  nav,
  links,
}: {
  span: Span
  tab: Tab
  onTabChange: (tab: Tab) => void
  nav: NavProps
  links: SpanLinks
}) {
  const tabs: { key: Tab; label: string }[] = [
    { key: 'input', label: '输入' },
    { key: 'output', label: '输出' },
  ]
  const meta = spanMeta(span)
  const Icon = meta.icon
  const failed = span.kind === 'tool' && span.call.failed
  const navButton =
    'flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-30'
  return (
    <div className="flex min-h-full flex-col text-xs">
      <div className="sticky top-0 z-10 flex shrink-0 items-center gap-2.5 border-b border-border bg-card px-4 py-2.5">
        <span className={`flex size-7 shrink-0 items-center justify-center rounded-md ${meta.chip}`}>
          <Icon className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h3
            className={`truncate text-sm font-semibold ${span.kind === 'tool' ? 'font-mono' : ''} ${failed ? 'text-red-700 dark:text-red-400' : 'text-foreground'}`}
          >
            {span.label}
          </h3>
          <p className="truncate text-xs text-muted-foreground">
            {meta.kindLabel}
            {failed && <span className="text-red-600 dark:text-red-400"> · 执行失败</span>}
            {span.call.incomplete && <span className="text-amber-600 dark:text-amber-400"> · 未完成</span>}
          </p>
        </div>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {nav.position} / {nav.count}
        </span>
        <div className="flex shrink-0 items-center">
          <button
            type="button"
            onClick={nav.onPrev}
            disabled={!nav.onPrev}
            aria-label="上一个调用"
            title="上一个调用（↑）"
            className={navButton}
          >
            <ChevronLeft className="size-4" aria-hidden />
          </button>
          <button
            type="button"
            onClick={nav.onNext}
            disabled={!nav.onNext}
            aria-label="下一个调用"
            title="下一个调用（↓）"
            className={navButton}
          >
            <ChevronRight className="size-4" aria-hidden />
          </button>
        </div>
      </div>
      <div className="space-y-4 p-4">
        <Metadata span={span} />
        {links.parent && (
          <button
            type="button"
            onClick={() => links.parent && links.onJump(links.parent.key)}
            className="inline-flex items-center gap-1.5 rounded-md text-[13px] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <CornerLeftUp className="size-3.5" aria-hidden />由{' '}
            <span className="font-medium text-foreground underline-offset-4 hover:underline">{links.parent.label}</span> 发起
          </button>
        )}
        <div role="tablist" aria-label="查看内容" className="flex gap-4 border-b border-border">
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`span-tab-${t.key}`}
              aria-selected={tab === t.key}
              aria-controls="span-tabpanel"
              onClick={() => onTabChange(t.key)}
              className={`-mb-px border-b-2 pb-2 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                tab === t.key
                  ? 'border-primary font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="span-tabpanel" aria-labelledby={`span-tab-${tab}`}>
          {span.kind === 'llm' ? (
            tab === 'input' ? (
              <LlmInput call={span.call} index={span.index} />
            ) : (
              <LlmOutput call={span.call} links={links} />
            )
          ) : tab === 'input' ? (
            <ToolInput call={span.call} />
          ) : (
            <ToolOutput call={span.call} />
          )}
        </div>
      </div>
    </div>
  )
}

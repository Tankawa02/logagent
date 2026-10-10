import { ArrowUpRight, Bot, Brain, Check, ChevronDown, ChevronUp, Copy, Wrench } from 'lucide-react'
import type { ReactNode } from 'react'
import { formatDuration } from '../lib/format'
import { useCopy } from '../lib/hooks'
import type { LlmCall, TraceMessage, ToolCall, ToolRequest } from '../lib/types'

export type Span =
  | { kind: 'llm'; key: string; label: string; start: number; seconds: number; call: LlmCall; index: number }
  | { kind: 'tool'; key: string; label: string; start: number; seconds: number; call: ToolCall }

export type Tab = 'input' | 'output'

/** 详情面板里的跳转：模型调用 -> 它发起的工具，工具 -> 发起它的模型调用 */
export interface SpanLinks {
  toolByRequestId: Map<string, Span>
  parent: Span | null
  onJump: (key: string) => void
}

const ROLE_STYLE: Record<string, { label: string; className: string }> = {
  system: { label: 'System', className: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200' },
  user: { label: 'User', className: 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300' },
  assistant: { label: 'AI', className: 'bg-brand-100 text-brand-800 dark:bg-brand-950 dark:text-brand-300' },
  tool: { label: 'Tool', className: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300' },
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
  const role = ROLE_STYLE[message.role] ?? { label: message.role, className: 'bg-zinc-100 text-zinc-600' }
  // 系统提示和很长的工具结果默认收起，先看清整体顺序
  const long = message.role === 'system' || message.chars > 1200
  return (
    <li>
      <details open={!long} className="group rounded-md ring-1 ring-zinc-200 dark:ring-zinc-800">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-2.5 py-1.5 [&::-webkit-details-marker]:hidden">
          <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${role.className}`}>{role.label}</span>
          {message.name && <span className="font-mono text-xs text-zinc-600 dark:text-zinc-400">{message.name}</span>}
          <span className="ml-auto text-xs tabular-nums text-zinc-400">{message.chars.toLocaleString()} 字</span>
        </summary>
        <div className="space-y-2 border-t border-zinc-100 p-2 dark:border-zinc-800">
          {(message.text || !message.tool_requests?.length) && <TextBlock text={message.text} chars={message.chars} />}
          {message.tool_requests?.length ? <ToolRequestList requests={message.tool_requests} /> : null}
        </div>
      </details>
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

function LlmOutput({ call, links }: { call: LlmCall; links?: SpanLinks }) {
  const hasText = Boolean(call.output_text?.text)
  if (!call.output_text && !call.reasoning && !call.tool_requests) {
    return <p className="text-zinc-500">这一轮由旧版本记录，没有保存模型输出。</p>
  }
  return (
    <div className="space-y-3">
      {call.reasoning && (
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300 [&::-webkit-details-marker]:hidden">
            <Brain className="h-3.5 w-3.5 text-violet-500" aria-hidden />
            思考过程
            <span className="font-normal text-zinc-400">{call.reasoning.chars.toLocaleString()} 字 · 点击展开</span>
          </summary>
          <div className="mt-1.5">
            <TextBlock text={call.reasoning.text} chars={call.reasoning.chars} tone="muted" />
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
  return (
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
          {span.call.model && <span className="font-mono">{span.call.model}</span>}
          {span.call.finish_reason && <span>结束原因 {span.call.finish_reason}</span>}
        </>
      )}
      {span.kind === 'tool' && span.call.subagent && <span>子代理 {span.call.subagent}</span>}
    </div>
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
  const Icon = span.kind === 'llm' ? Bot : Wrench
  const failed = span.kind === 'tool' && span.call.failed
  const iconTone =
    span.kind === 'llm' ? 'text-brand-600' : failed ? 'text-red-600' : span.call.subagent ? 'text-violet-600' : 'text-amber-600'
  const navButton =
    'rounded p-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent dark:hover:bg-zinc-800 dark:hover:text-zinc-200'
  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 shrink-0 ${iconTone}`} aria-hidden />
        <h3
          className={`min-w-0 truncate font-mono text-sm font-semibold ${failed ? 'text-red-700 dark:text-red-400' : 'text-zinc-900 dark:text-zinc-50'}`}
        >
          {span.label}
        </h3>
        <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
          {span.kind === 'llm' ? '模型' : span.call.subagent ? '子代理' : '工具'}
        </span>
        {failed && (
          <span className="shrink-0 rounded bg-red-100 px-1.5 py-0.5 text-[11px] text-red-700 dark:bg-red-950 dark:text-red-300">失败</span>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          <span className="mr-1 font-mono text-[11px] tabular-nums text-zinc-400">
            {nav.position} / {nav.count}
          </span>
          <button
            type="button"
            onClick={nav.onPrev}
            disabled={!nav.onPrev}
            aria-label="上一个调用"
            title="上一个调用（↑）"
            className={navButton}
          >
            <ChevronUp className="h-4 w-4" aria-hidden />
          </button>
          <button
            type="button"
            onClick={nav.onNext}
            disabled={!nav.onNext}
            aria-label="下一个调用"
            title="下一个调用（↓）"
            className={navButton}
          >
            <ChevronDown className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>
      <Metadata span={span} />
      {links.parent && (
        <p className="text-zinc-500">
          由{' '}
          <button
            type="button"
            onClick={() => links.parent && links.onJump(links.parent.key)}
            className="inline-flex items-center gap-0.5 font-mono text-brand-700 underline-offset-2 hover:underline dark:text-brand-400"
          >
            {links.parent.label}
          </button>{' '}
          发起
        </p>
      )}
      <div role="tablist" aria-label="查看内容" className="inline-flex rounded-md bg-zinc-200/70 p-0.5 dark:bg-zinc-800">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`span-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls="span-tabpanel"
            onClick={() => onTabChange(t.key)}
            className={`rounded px-3 py-1 ${tab === t.key ? 'bg-white font-medium text-zinc-900 shadow-xs dark:bg-zinc-950 dark:text-zinc-100' : 'text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-200'}`}
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
  )
}

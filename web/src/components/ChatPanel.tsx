import { fetchServerSentEvents, useChat, type UIMessage } from '@tanstack/ai-react'
import { Activity as ActivityIcon, ArrowUp, Check, ChevronDown, Copy, FileSearch, Square, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api, CSRF_HEADERS } from '../lib/api'
import { ASSESSMENT, CHECK_STATUS, visibleReport } from '../lib/format'
import { takePendingQuestion } from '../lib/pending'
import type { SourceTarget, TurnBrief } from '../lib/types'
import { Markdown } from './Markdown'
import { Badge, ErrorBox, Spinner } from './ui'

interface ToolActivity {
  id: string
  label: string
  name: string
  detail: string
  note?: string
  subagent?: string
  summary?: string
  failed?: boolean
  seconds?: number
  done: boolean
}

export interface AskRequest {
  text: string
  nonce: number
}

const ARG_KEYS = ['pattern', 'key', 'rel_path', 'path', 'description', 'code_dir', 'since']

const FOLLOW_UPS = ['这些失败的请求有什么共同点？', '给出修复建议和验证步骤', '还有没有被忽略的其他异常？']

function describeArgs(args: Record<string, unknown> | undefined): string {
  if (!args) return ''
  for (const key of ARG_KEYS) {
    const value = args[key]
    if (typeof value === 'string' && value) {
      const text = key === 'path' || key === 'code_dir' ? value.split(/[\\/]/).pop() || value : value
      return text.length > 60 ? `${text.slice(0, 59)}…` : text
    }
  }
  return ''
}

function historyMessages(turns: TurnBrief[]): UIMessage[] {
  return turns.flatMap((t) => [
    { id: `turn-${t.turn}-q`, role: 'user' as const, parts: [{ type: 'text' as const, content: t.question }] },
    {
      id: `turn-${t.turn}-a`,
      role: 'assistant' as const,
      parts: [{ type: 'text' as const, content: t.summary || '（本轮没有保存一句话结论，点下方查看完整报告）' }],
      metadata: { turn: t.turn },
    },
  ])
}

export function ChatPanel({
  session,
  turns,
  ask,
  onTurnSaved,
  onShowTurn,
  onOpen,
}: {
  session: string
  turns: TurnBrief[]
  ask: AskRequest | null
  onTurnSaved: (turn: number) => void
  onShowTurn: (turn: number) => void
  onOpen: (target: SourceTarget) => void
}) {
  const [tools, setTools] = useState<ToolActivity[]>([])
  const [draft, setDraft] = useState('')
  const [serverError, setServerError] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [savedTurn, setSavedTurn] = useState<number | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)

  // connection / initialMessages 变化会重建 ChatClient：只在切换会话时创建一次
  const connection = useMemo(() => fetchServerSentEvents(api.chatUrl(session), { headers: CSRF_HEADERS }), [session])
  const initialMessages = useMemo(() => historyMessages(turns), [session]) // eslint-disable-line react-hooks/exhaustive-deps
  const briefs = useMemo(() => new Map(turns.map((t) => [t.turn, t])), [turns])

  const { messages, sendMessage, isLoading, error, stop } = useChat({
    connection,
    threadId: session,
    initialMessages,
    onCustomEvent: (name, value) => {
      const data = (value ?? {}) as Record<string, any>
      if (name === 'log_agent.draft') {
        setDraft((current) => (data.reset ? '' : current + (data.delta ?? '')))
      } else if (name === 'log_agent.tool_start') {
        setTools((list) => [
          ...list,
          { id: data.id, name: data.name, label: data.label, detail: describeArgs(data.args), note: data.note, done: false },
        ])
      } else if (name === 'log_agent.tool_end') {
        setTools((list) => {
          const done = { summary: data.summary, failed: data.failed, seconds: data.seconds, subagent: data.subagent, done: true }
          // 子代理内部的工具没有单独的 start 事件，结束时直接补一行
          if (data.subagent || !list.some((t) => t.id === data.id)) {
            return [...list, { id: `${data.id}-${list.length}`, name: data.name, label: data.label, detail: describeArgs(data.args), ...done }]
          }
          return list.map((t) => (t.id === data.id ? { ...t, ...done } : t))
        })
      } else if (name === 'log_agent.turn' && typeof data.turn === 'number') {
        setSavedTurn(data.turn)
        onTurnSaved(data.turn)
      } else if (name === 'log_agent.error') {
        setServerError(String(data.message ?? '未知错误'))
      }
    },
  })

  function submit(text: string) {
    const question = text.trim()
    if (!question || isLoading) return
    setTools([])
    setDraft('')
    setServerError(null)
    setSavedTurn(null)
    void sendMessage(question)
  }

  useEffect(() => {
    if (ask) submit(ask.text)
  }, [ask?.nonce]) // eslint-disable-line react-hooks/exhaustive-deps

  // 新建分析页交接过来的第一个问题；延迟一拍，StrictMode 的二次挂载只会真正发一次
  useEffect(() => {
    const timer = setTimeout(() => {
      const question = takePendingQuestion(session)
      if (question) submit(question)
    }, 0)
    return () => clearTimeout(timer)
  }, [session]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, tools.length, draft, isLoading])

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    submit(input)
    setInput('')
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
    onSubmit(event)
  }

  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')
  const lastUserIndex = messages.map((m) => m.role).lastIndexOf('user')
  const showActivity = isLoading || tools.length > 0

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
          {messages.length === 0 && !isLoading && (
            <div className="py-16 text-center">
              <p className="text-sm text-zinc-500">还没有对话记录。在下方提一个问题，agent 会读取这个会话的日志与源码开始排查。</p>
            </div>
          )}
          {messages.map((message, index) => {
            const text = message.parts.map((p) => (p.type === 'text' ? p.content : '')).join('')
            const turn = (message.metadata as { turn?: number } | undefined)?.turn
            const node =
              message.role === 'user' ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-zinc-100 px-4 py-2.5 text-sm leading-relaxed text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
                    {text}
                  </div>
                </div>
              ) : (
                <AssistantMessage
                  key={message.id}
                  text={text}
                  brief={turn !== undefined ? briefs.get(turn) : undefined}
                  historic={turn !== undefined}
                  savedTurn={message === lastAssistant && turn === undefined && !isLoading ? savedTurn : null}
                  onOpen={onOpen}
                  onShowTurn={onShowTurn}
                  turn={turn}
                />
              )
            if (index === lastUserIndex && showActivity) {
              return [node, <Activity key="activity" tools={tools} draft={isLoading ? draft : ''} running={isLoading} />]
            }
            return node
          })}
          {(serverError || error) && <ErrorBox error={serverError ?? error} />}
        </div>
      </div>

      <div className="border-t border-zinc-100 bg-white px-4 pb-4 pt-3 dark:border-zinc-900 dark:bg-zinc-950">
        <div className="mx-auto max-w-3xl space-y-2">
          {!isLoading && messages.length > 0 && !input && (
            <div className="flex flex-wrap gap-1.5">
              {FOLLOW_UPS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => {
                    setInput(s)
                    textarea.current?.focus()
                  }}
                  className="rounded-full border border-zinc-200 px-2.5 py-1 text-xs text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <form
            onSubmit={onSubmit}
            className="flex items-end gap-2 rounded-2xl border border-zinc-300 bg-white p-2 shadow-xs focus-within:border-sky-500 focus-within:ring-2 focus-within:ring-sky-500/20 dark:border-zinc-700 dark:bg-zinc-900"
          >
            <label htmlFor="chat-input" className="sr-only">
              追问
            </label>
            <textarea
              id="chat-input"
              ref={textarea}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              rows={1}
              placeholder={isLoading ? '分析进行中…' : '继续追问（Enter 发送，Shift+Enter 换行）'}
              className="max-h-48 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm leading-relaxed outline-none [field-sizing:content] placeholder:text-zinc-400"
            />
            {isLoading ? (
              <button
                type="button"
                onClick={stop}
                aria-label="停止"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                aria-label="发送"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-white hover:bg-zinc-700 disabled:opacity-30 dark:bg-zinc-100 dark:text-zinc-900"
              >
                <ArrowUp className="h-4 w-4" />
              </button>
            )}
          </form>
          <p className="text-center text-xs text-zinc-400">点停止或关闭页面会中断本轮，已输出的部分照常保存。</p>
        </div>
      </div>
    </div>
  )
}

function AssistantMessage({
  text,
  brief,
  historic,
  savedTurn,
  turn,
  onOpen,
  onShowTurn,
}: {
  text: string
  brief?: TurnBrief
  historic: boolean
  savedTurn: number | null
  turn?: number
  onOpen: (target: SourceTarget) => void
  onShowTurn: (turn: number) => void
}) {
  const [copied, setCopied] = useState(false)
  const reportTurn = turn ?? savedTurn ?? undefined
  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-sky-600 text-white" aria-hidden>
        <ActivityIcon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        {brief && (brief.assessment || brief.evidence_status || brief.status === 'error' || brief.status === 'interrupted') && (
          <div className="flex flex-wrap gap-1.5">
            {brief.assessment && <Badge tone={ASSESSMENT[brief.assessment].tone}>{ASSESSMENT[brief.assessment].label}</Badge>}
            {brief.evidence_status && (
              <Badge tone={CHECK_STATUS[brief.evidence_status].tone}>{CHECK_STATUS[brief.evidence_status].label}</Badge>
            )}
            {brief.status === 'error' && <Badge tone="red">出错</Badge>}
            {brief.status === 'interrupted' && <Badge tone="amber">已中断</Badge>}
          </div>
        )}
        {historic ? (
          <p className="text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">{text}</p>
        ) : (
          <Markdown text={visibleReport(text)} onOpen={onOpen} />
        )}
        <div className="flex items-center gap-1">
          {reportTurn !== undefined && (
            <button
              type="button"
              onClick={() => onShowTurn(reportTurn)}
              className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-sky-700 hover:bg-sky-50 dark:text-sky-300 dark:hover:bg-sky-950/40"
            >
              <FileSearch className="h-3.5 w-3.5" aria-hidden />
              {historic ? `查看第 ${reportTurn} 轮报告与证据` : `已保存为第 ${reportTurn} 轮 · 查看结构化报告`}
            </button>
          )}
          {!historic && text && (
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(visibleReport(text)).then(() => {
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1500)
                })
              }}
              aria-label="复制回答"
              className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function Activity({ tools, draft, running }: { tools: ToolActivity[]; draft: string; running: boolean }) {
  const [open, setOpen] = useState(true)
  const failed = tools.filter((t) => t.failed).length
  return (
    <div className="ml-10 overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50/60 text-xs dark:border-zinc-800 dark:bg-zinc-900/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 font-medium text-zinc-600 dark:text-zinc-300"
      >
        {running ? <Spinner /> : <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden />}
        <span>{running ? `正在取证 · 已执行 ${tools.length} 步` : `本轮取证 ${tools.length} 步${failed ? `，${failed} 步失败` : ''}`}</span>
        <ChevronDown className={`ml-auto h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {tools.length === 0 && running && <p className="text-zinc-400">正在加载模型与工具…</p>}
          <ol className="space-y-1">
            {tools.map((tool) => (
              <li key={tool.id} className={`flex items-center gap-2 ${tool.subagent ? 'pl-4' : ''}`}>
                <span className="flex w-3.5 shrink-0 justify-center">
                  {tool.done ? (
                    tool.failed ? (
                      <X className="h-3.5 w-3.5 text-red-500" aria-label="失败" />
                    ) : (
                      <Check className="h-3.5 w-3.5 text-emerald-600" aria-label="完成" />
                    )
                  ) : (
                    <Spinner className="h-3 w-3" />
                  )}
                </span>
                <span className="shrink-0 font-medium text-zinc-700 dark:text-zinc-200">{tool.label || tool.name}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-zinc-500" title={tool.summary || tool.detail}>
                  {tool.note ? `${tool.note} · ` : ''}
                  {tool.detail}
                  {tool.summary ? ` → ${tool.summary}` : ''}
                </span>
                {tool.seconds !== undefined && <span className="shrink-0 tabular-nums text-zinc-400">{tool.seconds.toFixed(1)}s</span>}
              </li>
            ))}
          </ol>
          {draft && (
            <p className="line-clamp-4 whitespace-pre-wrap border-t border-zinc-200 pt-1.5 text-zinc-500 dark:border-zinc-800">
              {visibleReport(draft)}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

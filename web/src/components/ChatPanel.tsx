import { fetchServerSentEvents, useChat, type UIMessage } from '@tanstack/ai-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api, CSRF_HEADERS } from '../lib/api'
import { visibleReport } from '../lib/format'
import type { SourceTarget, TurnBrief } from '../lib/types'
import { Markdown } from './Markdown'
import { Button, ErrorBox, Spinner } from './ui'

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
      parts: [{ type: 'text' as const, content: t.summary || '（本轮没有保存一句话结论）' }],
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
  const savedTurn = useRef<Map<string, number>>(new Map())
  const bottom = useRef<HTMLDivElement>(null)

  // connection / initialMessages 变化会重建 ChatClient：只在切换会话时创建一次
  const connection = useMemo(() => fetchServerSentEvents(api.chatUrl(session), { headers: CSRF_HEADERS }), [session])
  const initialMessages = useMemo(() => historyMessages(turns), [session]) // eslint-disable-line react-hooks/exhaustive-deps

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
          const done = {
            summary: data.summary,
            failed: data.failed,
            seconds: data.seconds,
            subagent: data.subagent,
            done: true,
          }
          // 子代理内部的工具没有单独的 start 事件，结束时直接补一行
          if (data.subagent || !list.some((t) => t.id === data.id)) {
            return [...list, { id: `${data.id}-${list.length}`, name: data.name, label: data.label, detail: describeArgs(data.args), ...done }]
          }
          return list.map((t) => (t.id === data.id ? { ...t, ...done } : t))
        })
      } else if (name === 'log_agent.turn' && typeof data.turn === 'number') {
        savedTurn.current.set('pending', data.turn)
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
    savedTurn.current.delete('pending')
    void sendMessage(question)
  }

  useEffect(() => {
    if (ask) submit(ask.text)
  }, [ask?.nonce]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, tools.length, isLoading])

  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    submit(input)
    setInput('')
  }

  return (
    <div className="flex h-full min-h-[420px] flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
        {messages.length === 0 && (
          <p className="text-sm text-gray-500">还没有对话记录。提一个问题，agent 会沿用本会话的日志、源码和分析设置继续排查。</p>
        )}
        {messages.map((message) => {
          const text = message.parts.map((p) => (p.type === 'text' ? p.content : '')).join('')
          const turn = (message.metadata as { turn?: number } | undefined)?.turn
          if (message.role === 'user') {
            return (
              <div key={message.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-gray-900 px-3 py-2 text-sm text-white dark:bg-gray-100 dark:text-gray-900">
                  {text}
                </div>
              </div>
            )
          }
          const live = message === lastAssistant && turn === undefined
          return (
            <div key={message.id} className="space-y-1">
              <div className="rounded-lg border border-gray-200 px-3 py-2 dark:border-gray-800">
                {turn !== undefined ? (
                  <p className="text-sm text-gray-700 dark:text-gray-300">{text}</p>
                ) : (
                  <Markdown text={visibleReport(text)} onOpen={onOpen} />
                )}
              </div>
              {turn !== undefined && (
                <button type="button" onClick={() => onShowTurn(turn)} className="text-xs text-sky-700 hover:underline dark:text-sky-300">
                  查看第 {turn} 轮完整报告与证据 →
                </button>
              )}
              {live && !isLoading && savedTurn.current.has('pending') && (
                <button
                  type="button"
                  onClick={() => onShowTurn(savedTurn.current.get('pending')!)}
                  className="text-xs text-sky-700 hover:underline dark:text-sky-300"
                >
                  已保存为第 {savedTurn.current.get('pending')} 轮，查看结构化报告与证据核对 →
                </button>
              )}
            </div>
          )
        })}
        {(isLoading || tools.length > 0) && <Activity tools={tools} draft={isLoading ? draft : ''} running={isLoading} />}
        {(serverError || error) && <ErrorBox error={serverError ?? error} />}
        <div ref={bottom} />
      </div>
      <form onSubmit={onSubmit} className="border-t border-gray-200 p-3 dark:border-gray-800">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSubmit(e)
          }}
          rows={3}
          placeholder="继续追问，例如：这些失败的请求有什么共同点？（Ctrl/⌘ + Enter 发送）"
          className="w-full resize-y rounded-md border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus:border-gray-500 focus:ring-1 focus:ring-gray-400 dark:border-gray-700 dark:bg-gray-950"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <p className="text-xs text-gray-400">关闭页面或点停止会中断本轮，已输出的部分照常保存。</p>
          {isLoading ? (
            <Button variant="danger" onClick={stop}>
              停止
            </Button>
          ) : (
            <Button variant="primary" type="submit" disabled={!input.trim()}>
              发送
            </Button>
          )}
        </div>
      </form>
    </div>
  )
}

function Activity({ tools, draft, running }: { tools: ToolActivity[]; draft: string; running: boolean }) {
  return (
    <div className="space-y-1.5 rounded-lg bg-gray-50 px-3 py-2 text-xs dark:bg-gray-900">
      <div className="flex items-center gap-2 font-medium text-gray-600 dark:text-gray-300">
        {running && <Spinner />}
        {running ? '分析中…' : `本轮取证 ${tools.length} 步`}
      </div>
      <ol className="space-y-1">
        {tools.map((tool) => (
          <li key={tool.id} className={`flex gap-2 ${tool.subagent ? 'pl-4' : ''}`}>
            <span className="w-3 shrink-0 text-center">
              {tool.done ? (tool.failed ? <span className="text-red-500">✗</span> : <span className="text-emerald-600">✓</span>) : <Spinner className="h-3 w-3" />}
            </span>
            <span className="shrink-0 font-medium text-gray-700 dark:text-gray-200">{tool.label || tool.name}</span>
            <span className="min-w-0 flex-1 truncate text-gray-500" title={tool.summary || tool.detail}>
              {tool.note ? `${tool.note} · ` : ''}
              {tool.detail}
              {tool.summary ? ` → ${tool.summary}` : ''}
            </span>
            {tool.seconds !== undefined && <span className="tabular-nums text-gray-400">{tool.seconds.toFixed(1)}s</span>}
          </li>
        ))}
      </ol>
      {draft && (
        <p className="line-clamp-4 whitespace-pre-wrap border-t border-gray-200 pt-1.5 text-gray-500 dark:border-gray-800">
          {visibleReport(draft)}
        </p>
      )}
    </div>
  )
}

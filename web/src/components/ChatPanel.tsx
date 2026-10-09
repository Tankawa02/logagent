import { useChat, type UIMessage } from '@tanstack/ai-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity as ActivityIcon, ArrowDown, ArrowUp, Check, Copy, FileSearch, ListPlus, Plus, Square, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api, scopeKey } from '../lib/api'
import { followUpsFor } from '../lib/follow-ups'
import { ASSESSMENT, CHECK_STATUS, visibleReport } from '../lib/format'
import { useCopy, useStickToBottom } from '../lib/hooks'
import { liveChatConnection } from '../lib/live-chat'
import { takePendingQuestion } from '../lib/pending'
import type { LiveRun, SourceTarget, TurnBrief } from '../lib/types'
import { ActivityTimeline, type ToolActivity } from './ActivityTimeline'
import { Markdown } from './Markdown'
import { MemoryConfirm, MemoryStatus } from './SessionMemory'
import { Badge, ErrorBox, Spinner } from './ui'

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
      parts: [{ type: 'text' as const, content: t.summary || '（本轮没有保存一句话结论，点下方查看完整报告）' }],
      metadata: { turn: t.turn },
    },
  ])
}

export function ChatPanel({
  session,
  turns,
  live,
  ask,
  onTurnSaved,
  onShowTurn,
  onOpen,
}: {
  session: string
  turns: TurnBrief[]
  /** 打开页面时服务端正在跑的那一轮（切走后再切回来），需要接回去继续显示 */
  live: LiveRun | null
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
  const [stopping, setStopping] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const client = useQueryClient()
  const memoryKey = ['memory', 'session', session]
  const memory = useQuery({ queryKey: memoryKey, queryFn: () => api.sessionMemory(session) })

  // 服务端还标着「进行中」、但历史里已经有这一轮（刚存档、还没收尾）时不再接，否则同一轮会出现两遍
  const liveTurn = live?.saved_turn ?? live?.turn
  const alreadySaved = typeof liveTurn === 'number' && turns.some((t) => t.turn >= liveTurn)
  const resumeQuestion = live?.active && !alreadySaved ? (live.question ?? '') : null
  // connection / initialMessages 变化会重建 ChatClient：只在切换会话时创建一次
  const connection = useMemo(() => liveChatConnection(session), [session])
  const initialMessages = useMemo(() => {
    const history = historyMessages(turns)
    if (resumeQuestion === null) return history
    // 还没存档的那一轮：先把问题摆上，reload() 会接回服务端的事件流，把过程和回答重放出来
    return [
      ...history,
      { id: `live-${live?.run_id ?? 'run'}-q`, role: 'user' as const, parts: [{ type: 'text' as const, content: resumeQuestion }] },
    ]
  }, [session]) // eslint-disable-line react-hooks/exhaustive-deps
  const briefs = useMemo(() => new Map(turns.map((t) => [t.turn, t])), [turns])

  const { messages, sendMessage, reload, isLoading, error } = useChat({
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
            return [
              ...list,
              { id: `${data.id}-${list.length}`, name: data.name, label: data.label, detail: describeArgs(data.args), ...done },
            ]
          }
          return list.map((t) => (t.id === data.id ? { ...t, ...done } : t))
        })
      } else if (name === 'log_agent.turn' && typeof data.turn === 'number') {
        setSavedTurn(data.turn)
        onTurnSaved(data.turn)
      } else if (name === 'log_agent.memory') {
        // 本轮登记了新的候选：重新拉取，待确认的直接显示在回答下方
        void client.invalidateQueries({ queryKey: memoryKey })
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
    scrollToBottom('auto')
    void sendMessage(question)
  }

  // 接回切走前还没跑完的那一轮；延迟一拍，StrictMode 的二次挂载只会真正接一次
  useEffect(() => {
    if (resumeQuestion === null) return
    const timer = setTimeout(() => {
      setTools([])
      setDraft('')
      setServerError(null)
      setSavedTurn(null)
      connection.resumeNext(live?.run_id)
      void reload()
    }, 0)
    return () => clearTimeout(timer)
  }, [session]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isLoading) setStopping(false)
  }, [isLoading])

  // 分析在服务端独立运行，断开连接不会中断它，所以不能靠断开来「停止」：
  // 带上 run_id 明确告诉服务端停哪一轮（请求还没到也会先记下），然后继续接收，直到它按中断收尾、存档
  function requestStop() {
    setStopping(true)
    api.stopChat(session, connection.currentRunId()).catch((err: unknown) => {
      setStopping(false)
      setServerError(`停止失败：${err instanceof Error ? err.message : String(err)}，分析仍在进行，可以再点一次停止。`)
    })
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

  const { atBottom, scrollToBottom } = useStickToBottom(scroller, [messages, tools.length, draft, isLoading, memory.data?.pending.length])

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    submit(input)
    setInput('')
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
    onSubmit(event)
  }

  // 追问根据最近一轮的结构化报告生成；和报告面板共用同一份查询缓存
  const latestTurn = turns.at(-1)?.turn
  const ownerScope = useMemo(() => ({ kind: 'owner' as const, name: session }), [session])
  const latestPayload = useQuery({
    queryKey: [...scopeKey(ownerScope), 'turn', latestTurn],
    queryFn: () => api.turn(ownerScope, latestTurn!),
    enabled: latestTurn !== undefined && !isLoading,
  })
  const followUps = useMemo(() => followUpsFor(latestPayload.data), [latestPayload.data])

  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')
  const lastUserIndex = messages.map((m) => m.role).lastIndexOf('user')
  const showActivity = isLoading || tools.length > 0

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="relative min-h-0 flex-1">
        <div ref={scroller} className="h-full overflow-auto">
          <div className="mx-auto max-w-3xl px-4 pb-10 pt-8 sm:px-6">
            {messages.length === 0 && !isLoading && (
              <div className="py-20 text-center">
                <p className="text-sm text-zinc-500">还没有对话记录。在下方提一个问题，agent 会读取这个会话的日志与源码开始排查。</p>
              </div>
            )}
            {messages.map((message, index) => {
              const text = message.parts.map((p) => (p.type === 'text' ? p.content : '')).join('')
              const turn = (message.metadata as { turn?: number } | undefined)?.turn
              const node =
                message.role === 'user' ? (
                  <h2
                    key={message.id}
                    className={`whitespace-pre-wrap text-pretty text-xl font-medium leading-snug tracking-tight text-zinc-900 sm:text-2xl dark:text-zinc-50 ${
                      index > 0 ? 'mt-10 border-t border-zinc-200/80 pt-10 dark:border-zinc-800' : ''
                    }`}
                  >
                    {text}
                  </h2>
                ) : (
                  <AssistantMessage
                    key={message.id}
                    text={text}
                    brief={turn !== undefined ? briefs.get(turn) : undefined}
                    historic={turn !== undefined}
                    savedTurn={
                      // 被中断、还没产出回答的一轮不能把「已保存」标到上一轮的回答上
                      message === lastAssistant && index > lastUserIndex && turn === undefined && !isLoading ? savedTurn : null
                    }
                    onOpen={onOpen}
                    onShowTurn={onShowTurn}
                    turn={turn}
                  />
                )
              if (index === lastUserIndex && showActivity) {
                return [
                  node,
                  <div key="activity" className="mt-6">
                    <ActivityTimeline tools={tools} draft={isLoading ? draft : ''} running={isLoading} />
                  </div>,
                ]
              }
              return node
            })}
            {(serverError || error) && (
              <div className="mt-6">
                <ErrorBox error={serverError ?? error} />
              </div>
            )}
            {!isLoading && memory.data && (
              <div className="mt-6">
                <MemoryConfirm memory={memory.data} />
              </div>
            )}
            {!isLoading && messages.length > 0 && (
              <section aria-labelledby="related-heading" className="mt-10">
                <h3 id="related-heading" className="mb-2 flex items-center gap-2 text-sm font-medium text-zinc-900 dark:text-zinc-100">
                  <ListPlus className="h-4 w-4 text-brand-600" aria-hidden />
                  相关追问
                </h3>
                <ul className="divide-y divide-zinc-200/80 border-y border-zinc-200/80 dark:divide-zinc-800 dark:border-zinc-800">
                  {followUps.map((s) => (
                    <li key={s}>
                      <button
                        type="button"
                        onClick={() => {
                          setInput(s)
                          textarea.current?.focus()
                        }}
                        className="group flex w-full items-center justify-between gap-3 py-3 text-left text-sm text-zinc-700 transition-colors hover:text-brand-700 dark:text-zinc-300 dark:hover:text-brand-300"
                      >
                        {s}
                        <Plus className="h-4 w-4 shrink-0 text-zinc-400 transition-colors group-hover:text-brand-600" aria-hidden />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </div>
        {!atBottom && (
          <button
            type="button"
            onClick={() => scrollToBottom()}
            className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 shadow-md transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <ArrowDown className="h-3.5 w-3.5" aria-hidden />
            {isLoading ? '回到最新输出' : '回到底部'}
          </button>
        )}
      </div>

      <div className="bg-gradient-to-t from-paper via-paper to-transparent px-4 pb-4 pt-2 dark:from-paper-dark dark:via-paper-dark">
        <div className="mx-auto max-w-3xl space-y-2 sm:px-2">
          <form
            onSubmit={onSubmit}
            className="rounded-3xl border border-zinc-200 bg-white p-2 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_-12px_rgba(0,0,0,0.1)] transition-shadow focus-within:border-brand-300 focus-within:ring-4 focus-within:ring-brand-500/10 dark:border-zinc-800 dark:bg-zinc-900 dark:focus-within:border-brand-800"
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
              placeholder={isLoading ? '分析进行中…' : '继续追问…'}
              className="block max-h-48 min-h-10 w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-relaxed outline-none [field-sizing:content] placeholder:text-zinc-400"
            />
            <div className="flex items-center justify-between gap-2 pl-1">
              <div className="min-w-0">{memory.data && <MemoryStatus memory={memory.data} />}</div>
              <div className="flex items-center gap-3">
                <span className="hidden text-xs text-zinc-400 sm:inline">Enter 发送 · Shift+Enter 换行</span>
                {isLoading ? (
                  <button
                    type="button"
                    onClick={requestStop}
                    disabled={stopping}
                    aria-label={stopping ? '正在停止' : '停止'}
                    title={stopping ? '正在停止，等当前步骤结束后保存已输出的部分' : '停止本轮'}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-white transition-colors hover:bg-zinc-700 disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900"
                  >
                    {stopping ? <Spinner /> : <Square className="h-3.5 w-3.5 fill-current" />}
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!input.trim()}
                    aria-label="发送"
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white shadow-sm transition-colors hover:bg-brand-700 disabled:bg-zinc-200 disabled:text-zinc-400 disabled:shadow-none dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
                  >
                    <ArrowUp className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          </form>
          <p className="text-center text-xs text-zinc-400">
            {stopping
              ? '正在停止，当前步骤结束后会保存已输出的部分。'
              : '切到别的会话或关闭页面不会中断分析，回来可接着看；点停止才会中断本轮。'}
          </p>
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
  const { state: copyState, copy } = useCopy()
  const reportTurn = turn ?? savedTurn ?? undefined
  return (
    <article className="mt-6 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-sm font-medium text-zinc-900 dark:text-zinc-100">
          <ActivityIcon className="h-4 w-4 text-brand-600" aria-hidden />
          回答
        </span>
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
      </div>
      <Markdown text={historic ? text : visibleReport(text)} onOpen={onOpen} />
      <div className="-ml-2 flex flex-wrap items-center gap-1 pt-1">
        {reportTurn !== undefined && (
          <button
            type="button"
            onClick={() => onShowTurn(reportTurn)}
            className="flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-xs font-medium text-brand-700 transition-colors hover:bg-brand-50 dark:text-brand-300 dark:hover:bg-brand-950/40"
          >
            <FileSearch className="h-3.5 w-3.5" aria-hidden />
            {historic ? `查看第 ${reportTurn} 轮报告与证据` : `已保存为第 ${reportTurn} 轮 · 查看结构化报告`}
          </button>
        )}
        {!historic && text && (
          <button
            type="button"
            onClick={() => void copy(visibleReport(text))}
            aria-label={copyState === 'copied' ? '已复制' : copyState === 'failed' ? '复制失败' : '复制回答'}
            title={copyState === 'failed' ? '复制失败：浏览器拒绝了剪贴板访问' : undefined}
            className="flex items-center gap-1 rounded-full p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            {copyState === 'copied' ? (
              <Check className="h-3.5 w-3.5 text-emerald-600" />
            ) : copyState === 'failed' ? (
              <>
                <X className="h-3.5 w-3.5 text-red-600" />
                <span className="text-xs text-red-600">复制失败</span>
              </>
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
        )}
      </div>
    </article>
  )
}

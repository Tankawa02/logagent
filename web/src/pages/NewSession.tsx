import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { ArrowUp, ChevronDown, Settings2, Sparkles } from 'lucide-react'
import { useEffect, useState, type FormEvent, type KeyboardEvent } from 'react'
import { SourcePicker } from '../components/SourcePicker'
import { ErrorBox, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { setPendingQuestion } from '../lib/pending'

const SUGGESTIONS = [
  '这段时间有哪些错误？按影响大小排序，并给出根因',
  '服务为什么在凌晨出现大量超时？',
  '找出最早出现的异常，以及它引发的连锁错误',
  '对比基线时段，这次多出来的错误是什么？',
]

const FIELD =
  'w-full rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 dark:border-zinc-700 dark:bg-zinc-950'

export function NewSession({ from }: { from?: string }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [logs, setLogs] = useState<string[]>([])
  const [code, setCode] = useState<string[]>([])
  const [question, setQuestion] = useState('')
  const [model, setModel] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [timezone, setTimezone] = useState('')
  const [baseline, setBaseline] = useState('')
  const [encoding, setEncoding] = useState('')
  const [advanced, setAdvanced] = useState(false)

  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const models = useQuery({ queryKey: ['models'], queryFn: api.models, staleTime: 300_000, enabled: !!meta.data?.can_chat })
  const source = useQuery({
    queryKey: ['owner', from, 'session'],
    queryFn: () => api.session({ kind: 'owner', name: from! }),
    enabled: !!from,
  })

  useEffect(() => {
    if (!source.data) return
    setLogs(source.data.logs.filter((l) => l.exists !== false).map((l) => l.path))
    setCode(source.data.code.map((c) => c.path))
    setModel(source.data.model)
    const s = source.data.settings as Record<string, string | null>
    setSince(s.since ?? '')
    setUntil(s.until ?? '')
    setTimezone(s.timezone ?? '')
    setBaseline(s.baseline ?? '')
    setEncoding(s.encoding ?? '')
  }, [source.data])

  const create = useMutation({
    mutationFn: () =>
      api.createSession({
        logs,
        code,
        model: model || undefined,
        since: since || undefined,
        until: until || undefined,
        timezone: timezone || undefined,
        baseline: baseline || undefined,
        encoding: encoding || undefined,
      }),
    onSuccess: (session) => {
      setPendingQuestion(session.name, question.trim())
      void client.invalidateQueries({ queryKey: ['sessions'] })
      void navigate({ to: '/sessions/$name', params: { name: session.name } })
    },
  })

  const canChat = !!meta.data?.can_chat
  const ready = canChat && logs.length > 0 && question.trim().length > 0 && !create.isPending

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (ready) create.mutate()
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    if (ready) create.mutate()
  }

  const defaultModel = models.data?.default ?? meta.data?.default_model ?? ''

  return (
    <div className="h-full overflow-auto">
      <form onSubmit={onSubmit} className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-10 sm:py-16">
        <div className="space-y-2 text-center">
          <h1 className="text-balance text-2xl font-semibold tracking-tight sm:text-3xl">要排查什么问题？</h1>
          <p className="text-pretty text-sm text-zinc-500 dark:text-zinc-400">
            选好日志和源码，用自然语言提问。agent 会自己检索日志、对照代码，给出带证据行号的结论。
          </p>
        </div>

        {meta.data && !canChat && (
          <ErrorBox error="当前服务不能提问：启动 log-agent serve 时缺少 OPENAI_API_KEY，或使用了 --read-only。可以先运行 log-agent init 配置。" />
        )}

        <section className="space-y-5 rounded-xl border border-zinc-200 bg-white p-4 shadow-xs sm:p-5 dark:border-zinc-800 dark:bg-zinc-900">
          <SourcePicker kind="log" values={logs} onChange={setLogs} />
          <div className="border-t border-zinc-100 dark:border-zinc-800" />
          <SourcePicker kind="code" values={code} onChange={setCode} />
        </section>

        <section className="rounded-xl border border-zinc-200 bg-white shadow-xs focus-within:border-sky-500 focus-within:ring-2 focus-within:ring-sky-500/20 dark:border-zinc-800 dark:bg-zinc-900">
          <label htmlFor="question" className="sr-only">
            问题
          </label>
          <textarea
            id="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKey}
            rows={3}
            placeholder="描述你想排查的问题，例如：下单接口 14:00 之后大量 500，原因是什么？"
            className="block w-full resize-none rounded-t-xl bg-transparent px-4 pt-4 text-sm leading-relaxed outline-none placeholder:text-zinc-400"
          />
          <div className="flex items-center justify-between gap-2 px-3 pb-3">
            <button
              type="button"
              onClick={() => setAdvanced((v) => !v)}
              aria-expanded={advanced}
              className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            >
              <Settings2 className="h-3.5 w-3.5" aria-hidden />
              <span className="max-w-48 truncate">{model || defaultModel || '模型与时间范围'}</span>
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${advanced ? 'rotate-180' : ''}`} aria-hidden />
            </button>
            <div className="flex items-center gap-2">
              <span className="hidden text-xs text-zinc-400 sm:inline">Enter 发送 · Shift+Enter 换行</span>
              <button
                type="submit"
                disabled={!ready}
                aria-label="开始分析"
                className="flex h-8 w-8 items-center justify-center rounded-full bg-zinc-900 text-white transition-opacity hover:bg-zinc-700 disabled:opacity-30 dark:bg-zinc-100 dark:text-zinc-900"
              >
                {create.isPending ? <Spinner className="border-zinc-500 border-t-white" /> : <ArrowUp className="h-4 w-4" />}
              </button>
            </div>
          </div>

          {advanced && (
            <div className="grid gap-3 border-t border-zinc-100 p-4 sm:grid-cols-2 dark:border-zinc-800">
              <label className="space-y-1 sm:col-span-2">
                <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">模型</span>
                <input
                  list="model-options"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder={defaultModel ? `默认 ${defaultModel}` : 'provider:model'}
                  className={`${FIELD} font-mono`}
                />
                <datalist id="model-options">
                  {models.data?.models.map((m) => <option key={m} value={m} />)}
                </datalist>
                {models.isFetching && <span className="text-xs text-zinc-400">正在读取接口上的模型列表…</span>}
              </label>
              <label className="space-y-1">
                <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">开始时间</span>
                <input value={since} onChange={(e) => setSince(e.target.value)} placeholder="如 2026-10-08 14:00 或 2h" className={FIELD} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">结束时间</span>
                <input value={until} onChange={(e) => setUntil(e.target.value)} placeholder="不填表示到日志结尾" className={FIELD} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">时区</span>
                <input value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="如 Asia/Shanghai，默认 UTC" className={FIELD} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">日志编码</span>
                <input value={encoding} onChange={(e) => setEncoding(e.target.value)} placeholder="自动识别，如 gbk" className={FIELD} />
              </label>
              <label className="space-y-1 sm:col-span-2">
                <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">基线时段</span>
                <input
                  value={baseline}
                  onChange={(e) => setBaseline(e.target.value)}
                  placeholder="用于对比的正常时段，如 2026-10-07 14:00..2026-10-07 15:00"
                  className={FIELD}
                />
              </label>
            </div>
          )}
        </section>

        {!question && (
          <div className="flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setQuestion(s)}
                className="flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-xs text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:text-zinc-100"
              >
                <Sparkles className="h-3 w-3 text-sky-500" aria-hidden />
                {s}
              </button>
            ))}
          </div>
        )}

        {create.error && <ErrorBox error={create.error} />}
        {!logs.length && question.trim() && <p className="text-center text-xs text-zinc-500">先添加至少一个日志文件再开始分析。</p>}
      </form>
    </div>
  )
}

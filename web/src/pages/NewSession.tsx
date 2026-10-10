import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { ArrowRight, ArrowUp, Check, ChevronDown, Settings2, Sparkles } from 'lucide-react'
import { useEffect, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { isUnknownModel, ModelCombobox } from '../components/ModelCombobox'
import { SourcePicker, type SourceValidity } from '../components/SourcePicker'
import { ErrorBox, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { clearDraft, EMPTY_DRAFT, readDraft, writeDraft } from '../lib/draft'
import { setPendingQuestion } from '../lib/pending'
import { PRIMARY_INPUT_ATTR } from '../lib/shortcuts'
import { checkBound, checkRange, checkTimezone, type BoundCheck } from '../lib/time-input'

const SUGGESTIONS = [
  { tag: '错误汇总', text: '这段时间有哪些错误？按影响大小排序，并给出根因' },
  { tag: '性能', text: '服务为什么在凌晨出现大量超时？' },
  { tag: '连锁故障', text: '找出最早出现的异常，以及它引发的连锁错误' },
  { tag: '基线对比', text: '对比基线时段，这次多出来的错误是什么？' },
]

const FIELD =
  'w-full rounded-lg border bg-white px-3 py-2 text-sm outline-none transition-colors placeholder:text-zinc-400 focus:ring-4 dark:bg-zinc-950'
const FIELD_OK = 'border-zinc-200 focus:border-brand-400 focus:ring-brand-500/10 dark:border-zinc-700'
const FIELD_BAD = 'border-red-300 focus:border-red-400 focus:ring-red-500/10 dark:border-red-800'

const NO_ISSUES: SourceValidity = { invalid: 0, checking: 0 }

export function NewSession({ from }: { from?: string }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  // 复用来源时以来源会话为准；否则接着上次没提交的输入
  const [initial] = useState(() => (from ? EMPTY_DRAFT : readDraft()))
  const [logs, setLogs] = useState<string[]>(initial.logs)
  const [code, setCode] = useState<string[]>(initial.code)
  const [question, setQuestion] = useState(initial.question)
  const [model, setModel] = useState(initial.model)
  const [since, setSince] = useState(initial.since)
  const [until, setUntil] = useState(initial.until)
  const [timezone, setTimezone] = useState(initial.timezone)
  const [baseline, setBaseline] = useState(initial.baseline)
  const [encoding, setEncoding] = useState(initial.encoding)
  const [advanced, setAdvanced] = useState(false)
  const [questionFocused, setQuestionFocused] = useState(false)
  const [logValidity, setLogValidity] = useState(NO_ISSUES)
  const [codeValidity, setCodeValidity] = useState(NO_ISSUES)

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

  useEffect(() => {
    writeDraft({ question, logs, code, model, since, until, timezone, baseline, encoding })
  }, [question, logs, code, model, since, until, timezone, baseline, encoding])

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
      clearDraft()
      void client.invalidateQueries({ queryKey: ['sessions'] })
      void navigate({ to: '/sessions/$name', params: { name: session.name } })
    },
  })

  const sinceCheck = checkBound(since, false)
  const untilRaw = checkBound(until, true)
  const untilCheck: BoundCheck | null =
    untilRaw?.ok && sinceCheck?.ok && sinceCheck.order !== null && untilRaw.order !== null && untilRaw.order < sinceCheck.order
      ? { ok: false, error: '结束时间早于开始时间' }
      : untilRaw
  const timezoneCheck = checkTimezone(timezone)
  const baselineCheck = checkRange(baseline)
  const timeInvalid = [sinceCheck, untilCheck, timezoneCheck, baselineCheck].some((c) => c && !c.ok)

  const modelUnknown = !models.isFetching && isUnknownModel(model, models.data?.models ?? [])

  const canChat = !!meta.data?.can_chat
  const blocker = !canChat
    ? '还没有配置 API Key，暂时不能提问'
    : !question.trim()
      ? '先写下要排查的问题'
      : logs.length === 0
        ? '先添加至少一个日志文件'
        : logValidity.invalid + codeValidity.invalid > 0
          ? '有路径不存在或类型不对，先修正标红的路径'
          : logValidity.checking + codeValidity.checking > 0
            ? '正在检查路径…'
            : timeInvalid
              ? '时间设置有误，展开「模型与时间范围」查看'
              : modelUnknown
                ? '模型不在可用列表里，展开「模型与时间范围」重新选择'
                : null
  const ready = !blocker && !create.isPending

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
  // 收起时也能看出设了什么，免得忘了某个时间过滤
  const extras = [
    sinceCheck?.ok || untilCheck?.ok ? `${since.trim() || '开头'} → ${until.trim() || '结尾'}` : null,
    timezone.trim() || null,
    baseline.trim() ? '有基线' : null,
    encoding.trim() ? encoding.trim().toUpperCase() : null,
  ].filter(Boolean)
  // 用户已经开始填了才提示原因；空白页一进来就满屏提示没必要
  const showBlocker = !!blocker && canChat && (question.trim() !== '' || logs.length > 0)

  return (
    <div className="h-full overflow-auto">
      <form onSubmit={onSubmit} className="mx-auto flex min-h-full max-w-3xl flex-col justify-center gap-8 px-4 py-12 sm:py-16">
        <div className="space-y-3 text-center">
          <h1 className="text-balance text-3xl font-medium tracking-tight text-zinc-900 sm:text-4xl dark:text-zinc-50">要排查什么问题？</h1>
          <p className="mx-auto max-w-xl text-pretty text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
            选好日志和源码，用自然语言提问。agent 会自己检索日志、对照代码，给出带证据行号的结论。
          </p>
        </div>

        {meta.data && !canChat && (
          <div
            role="status"
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
          >
            <span>还不能提问：没有配置 API Key（或服务以 --read-only 启动）。</span>
            <Link
              to="/settings"
              className="inline-flex items-center gap-1 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-amber-900 ring-1 ring-amber-200 hover:bg-amber-100 dark:bg-amber-950 dark:text-amber-100 dark:ring-amber-800"
            >
              去设置
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </div>
        )}

        <section className="overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_-12px_rgba(0,0,0,0.08)] transition-shadow focus-within:border-brand-300 focus-within:ring-4 focus-within:ring-brand-500/10 dark:border-zinc-800 dark:bg-zinc-900 dark:focus-within:border-brand-800">
          <label htmlFor="question" className="sr-only">
            问题
          </label>
          <textarea
            id="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKey}
            onFocus={() => setQuestionFocused(true)}
            onBlur={() => setQuestionFocused(false)}
            rows={3}
            autoFocus
            {...{ [PRIMARY_INPUT_ATTR]: '' }}
            placeholder="描述你想排查的问题，例如：下单接口 14:00 之后大量 500，原因是什么？"
            className="block w-full resize-none bg-transparent px-5 pt-5 text-base leading-relaxed outline-none placeholder:text-zinc-400"
          />
          <div className="flex items-center justify-between gap-2 px-3 pb-3 pt-1">
            <button
              type="button"
              onClick={() => setAdvanced((v) => !v)}
              aria-expanded={advanced}
              aria-controls="advanced-settings"
              className={`flex min-w-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs transition-colors ${
                timeInvalid
                  ? 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300'
                  : advanced
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/50 dark:text-brand-300'
                    : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-zinc-200'
              }`}
            >
              <Settings2 className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="max-w-40 truncate font-mono">{model || defaultModel || '模型与时间范围'}</span>
              {extras.length > 0 && (
                <span className="hidden min-w-0 truncate text-zinc-500 sm:inline dark:text-zinc-400">· {extras.join(' · ')}</span>
              )}
              {timeInvalid && <span className="shrink-0">· 时间有误</span>}
              <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${advanced ? 'rotate-180' : ''}`} aria-hidden />
            </button>
            <div className="flex shrink-0 items-center gap-3">
              {questionFocused && <span className="hidden text-xs text-zinc-400 sm:inline">Enter 发送 · Shift+Enter 换行</span>}
              <button
                type="submit"
                disabled={!ready}
                aria-label="开始分析"
                aria-describedby={blocker ? 'start-blocker' : undefined}
                title={blocker ?? '开始分析'}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-600 text-white shadow-sm transition-colors hover:bg-brand-700 disabled:bg-zinc-200 disabled:text-zinc-400 disabled:shadow-none dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
              >
                {create.isPending ? <Spinner className="border-white/40 border-t-white" /> : <ArrowUp className="h-4 w-4" />}
              </button>
            </div>
          </div>

          {advanced && (
            <div id="advanced-settings" className="grid gap-3 border-t border-zinc-100 px-5 py-4 sm:grid-cols-2 dark:border-zinc-800">
              <div className="space-y-1.5 sm:col-span-2">
                <label htmlFor="model-input" className="block text-xs font-medium text-zinc-600 dark:text-zinc-300">
                  模型
                </label>
                <ModelCombobox
                  id="model-input"
                  value={model}
                  onChange={setModel}
                  options={models.data?.models ?? []}
                  defaultModel={defaultModel}
                  loading={models.isFetching}
                  className={`${FIELD} ${FIELD_OK}`}
                />
              </div>
              <TimeField
                id="since"
                label="开始时间"
                value={since}
                onChange={setSince}
                placeholder="如 2026-10-08 14:00 或 14:00"
                check={sinceCheck}
              />
              <TimeField
                id="until"
                label="结束时间"
                value={until}
                onChange={setUntil}
                placeholder="不填表示到日志结尾"
                check={untilCheck}
              />
              <TimeField
                id="timezone"
                label="时区"
                value={timezone}
                onChange={setTimezone}
                placeholder="如 Asia/Shanghai，默认 UTC"
                check={timezoneCheck}
                hint="日志里没写时区的时间按这里解释"
              />
              <TimeField
                id="encoding"
                label="日志编码"
                value={encoding}
                onChange={setEncoding}
                placeholder="自动识别，如 gbk"
                check={null}
              />
              <TimeField
                id="baseline"
                label="基线时段"
                value={baseline}
                onChange={setBaseline}
                placeholder="用于对比的正常时段，如 2026-10-07 14:00~2026-10-07 15:00"
                check={baselineCheck}
                wide
              />
            </div>
          )}

          <div className="space-y-4 border-t border-zinc-100 bg-zinc-50/70 px-5 py-4 dark:border-zinc-800 dark:bg-zinc-950/40">
            <SourcePicker kind="log" values={logs} onChange={setLogs} onValidity={setLogValidity} />
            <SourcePicker kind="code" values={code} onChange={setCode} onValidity={setCodeValidity} />
          </div>
        </section>

        {create.error && <ErrorBox error={create.error} />}
        <p id="start-blocker" aria-live="polite" className={`-mt-4 text-center text-xs text-zinc-500 ${showBlocker ? '' : 'sr-only'}`}>
          {blocker ?? ''}
        </p>

        {!question && (
          <div className="space-y-3">
            <h2 className="px-1 text-xs font-medium text-zinc-500 dark:text-zinc-400">试试这样问</h2>
            <div className="grid gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s.text}
                  type="button"
                  onClick={() => setQuestion(s.text)}
                  className="group flex flex-col items-start gap-1.5 rounded-2xl border border-zinc-200/80 bg-white/60 px-4 py-3 text-left text-sm leading-relaxed text-zinc-700 transition-colors hover:border-brand-200 hover:bg-white hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-300 dark:hover:border-brand-900 dark:hover:text-zinc-100"
                >
                  <span className="flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 dark:bg-brand-950/50 dark:text-brand-300">
                    <Sparkles className="h-3 w-3" aria-hidden />
                    {s.tag}
                  </span>
                  {s.text}
                </button>
              ))}
            </div>
          </div>
        )}
      </form>
    </div>
  )
}

function TimeField({
  id,
  label,
  value,
  onChange,
  placeholder,
  check,
  hint,
  wide = false,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder: string
  check: BoundCheck | null
  hint?: ReactNode
  wide?: boolean
}) {
  const bad = !!check && !check.ok
  const noteId = `${id}-note`
  return (
    <div className={`space-y-1.5 ${wide ? 'sm:col-span-2' : ''}`}>
      <label htmlFor={id} className="block text-xs font-medium text-zinc-600 dark:text-zinc-300">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        aria-invalid={bad || undefined}
        aria-describedby={check || hint ? noteId : undefined}
        className={`${FIELD} ${bad ? FIELD_BAD : FIELD_OK}`}
      />
      {check ? (
        <p
          id={noteId}
          className={`flex items-start gap-1 text-xs ${bad ? 'text-red-700 dark:text-red-300' : 'text-emerald-700 dark:text-emerald-400'}`}
        >
          {!bad && <Check className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />}
          {check.ok ? check.text : check.error}
        </p>
      ) : (
        hint && (
          <p id={noteId} className="text-xs text-zinc-500 dark:text-zinc-400">
            {hint}
          </p>
        )
      )}
    </div>
  )
}

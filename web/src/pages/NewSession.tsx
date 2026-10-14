import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { Activity, ArrowRight, ArrowUp, ChevronDown, GitCompare, Network, Settings2, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from 'react'
import { isUnknownModel, ModelCombobox } from '../components/ModelCombobox'
import { SourcePicker, type SourceValidity } from '../components/SourcePicker'
import { browserTimezone, FIELD, FIELD_OK, TimeField, TimePresets } from '../components/TimeField'
import { TopBar } from '../components/TopBar'
import { buttonClass, ErrorBox, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { clearDraft, EMPTY_DRAFT, readDraft, writeDraft } from '../lib/draft'
import { setPendingQuestion } from '../lib/pending'
import { PRIMARY_INPUT_ATTR } from '../lib/shortcuts'
import { checkBound, checkRange, checkTimezone, type BoundCheck } from '../lib/time-input'
import type { SessionSummary } from '../lib/types'

const SUGGESTIONS = [
  { tag: '错误汇总', icon: TriangleAlert, text: '这段时间有哪些错误？按影响大小排序，并给出根因' },
  { tag: '性能', icon: Activity, text: '服务为什么在凌晨出现大量超时？' },
  { tag: '连锁故障', icon: Network, text: '找出最早出现的异常，以及它引发的连锁错误' },
  { tag: '基线对比', icon: GitCompare, text: '对比基线时段，这次多出来的错误是什么？' },
]

const NO_ISSUES: SourceValidity = { invalid: 0, checking: 0 }
const RECENT_LIMIT = 4

/** 按最近更新的会话排列，取各自用过、仍然存在的日志和源码路径（去重） */
export function recentSources(sessions: SessionSummary[]): { logs: string[]; code: string[] } {
  const sorted = [...sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  const pick = (refs: (s: SessionSummary) => { path: string; exists?: boolean }[]) => {
    const seen = new Set<string>()
    for (const s of sorted) {
      for (const ref of refs(s)) {
        if (ref.exists === false || seen.has(ref.path)) continue
        seen.add(ref.path)
        if (seen.size >= RECENT_LIMIT) return [...seen]
      }
    }
    return [...seen]
  }
  return { logs: pick((s) => s.logs), code: pick((s) => s.code) }
}

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
  const [logValidity, setLogValidity] = useState(NO_ISSUES)
  const [codeValidity, setCodeValidity] = useState(NO_ISSUES)

  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const models = useQuery({ queryKey: ['models'], queryFn: api.models, staleTime: 300_000, enabled: !!meta.data?.can_chat })
  // 和侧边栏同一个查询（搜索词为空），共用缓存
  const history = useQuery({ queryKey: ['sessions', ''], queryFn: () => api.sessions('') })
  const recent = useMemo(() => recentSources(history.data ?? []), [history.data])
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
  const firstBadTime = [
    ['since', sinceCheck],
    ['until', untilCheck],
    ['timezone', timezoneCheck],
    ['baseline', baselineCheck],
  ].find(([, c]) => c && typeof c === 'object' && !c.ok)?.[0] as string | undefined
  // 每个原因带上要去的输入框：点一下就跳过去，不用自己找
  const block: { text: string; field?: string; advanced?: boolean } | null = !canChat
    ? { text: '还没有配置 API Key，暂时不能提问' }
    : !question.trim()
      ? { text: '先写下要排查的问题', field: 'question' }
      : logs.length === 0
        ? { text: '先添加至少一个日志文件', field: 'log-path-input' }
        : logValidity.invalid > 0
          ? { text: '有日志路径不存在或类型不对，先修正标红的路径', field: 'log-path-input' }
          : codeValidity.invalid > 0
            ? { text: '有源码目录不存在或类型不对，先修正标红的路径', field: 'code-path-input' }
            : logValidity.checking + codeValidity.checking > 0
              ? { text: '正在检查路径…' }
              : timeInvalid
                ? { text: '时间设置有误', field: firstBadTime, advanced: true }
                : modelUnknown
                  ? { text: '模型不在可用列表里，请重新选择', field: 'model-input', advanced: true }
                  : null
  const blocker = block?.text ?? null
  const ready = !blocker && !create.isPending

  function goToBlocker() {
    if (!block?.field) return
    const id = block.field
    if (block.advanced) setAdvanced(true)
    requestAnimationFrame(() => {
      const el = document.getElementById(id)
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      el?.focus({ preventScroll: true })
    })
  }

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
  // 收起时也能看出设了什么，免得漏了某个时间过滤
  const extras = [
    sinceCheck?.ok || untilCheck?.ok ? `${since.trim() || '开头'} → ${until.trim() || '结尾'}` : null,
    timezone.trim() || null,
    baseline.trim() ? '有基线' : null,
    encoding.trim() ? encoding.trim().toUpperCase() : null,
  ].filter(Boolean)
  // 用户已经开始填了才提示原因；空白页一进来就满屏提示没必要
  const showBlocker = !!blocker && canChat && (question.trim() !== '' || logs.length > 0)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar>
        <h1 className="sr-only">新建分析</h1>
        <span className="text-sm text-muted-foreground">新建分析</span>
      </TopBar>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 pb-12 pt-10 sm:px-6 sm:pt-16">
          <div className="text-center">
            <h2 className="text-balance font-heading text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
              要排查什么问题？
            </h2>
            <p className="mx-auto mt-2 max-w-lg text-pretty text-sm leading-6 text-muted-foreground">
              选好日志和源码，用自然语言提问。agent 会自己检索日志、对照代码，给出带证据行号的结论。
            </p>
          </div>

          {meta.data && !canChat && (
            <div
              role="status"
              className="mt-8 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
            >
              <span>还不能提问：没有配置 API Key（或服务以 --read-only 启动）。</span>
              <Link to="/settings" className={buttonClass('secondary', 'xs', 'border-amber-200 dark:border-amber-500/30')}>
                去设置
                <ArrowRight className="size-3.5" aria-hidden />
              </Link>
            </div>
          )}

          <form
            onSubmit={onSubmit}
            className="mt-8 rounded-2xl border border-border bg-card shadow-composer transition-colors focus-within:border-primary/40"
          >
            <label htmlFor="question" className="sr-only">
              要排查的问题
            </label>
            <textarea
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKey}
              rows={3}
              autoFocus
              {...{ [PRIMARY_INPUT_ATTR]: '' }}
              placeholder="描述你想排查的问题，例如：下单接口 14:00 之后大量 500，原因是什么？"
              className="block w-full resize-none rounded-t-2xl bg-transparent px-4 pb-2 pt-4 text-[15px] leading-6 text-foreground outline-none placeholder:text-muted-foreground"
            />

            <div className="grid gap-5 border-t border-border px-4 py-4 md:grid-cols-2">
              <SourcePicker kind="log" values={logs} onChange={setLogs} onValidity={setLogValidity} recent={recent.logs} />
              <SourcePicker kind="code" values={code} onChange={setCode} onValidity={setCodeValidity} recent={recent.code} />
            </div>

            {advanced && (
              <div id="advanced-settings" className="grid gap-3 border-t border-border px-4 py-4 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2">
                  <label htmlFor="model-input" className="block text-xs font-medium text-muted-foreground">
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
                <TimePresets
                  timezone={timezone.trim() || browserTimezone()}
                  active={!!(since || until)}
                  onPick={(s, u) => {
                    setSince(s)
                    setUntil(u)
                    // 快捷范围按浏览器所在时区算：没填时区时一并填上，免得按默认的 UTC 解释错位
                    if (!timezone.trim()) setTimezone(browserTimezone())
                  }}
                  onClear={() => {
                    setSince('')
                    setUntil('')
                  }}
                />
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

            <div className="flex items-center gap-3 border-t border-border px-3 py-2.5">
              <button
                type="button"
                onClick={() => setAdvanced((v) => !v)}
                aria-expanded={advanced}
                aria-controls="advanced-settings"
                title={[model || defaultModel, ...extras].filter(Boolean).join(' · ') || '模型与时间范围'}
                className={`inline-flex min-w-0 items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  timeInvalid
                    ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300'
                    : advanced
                      ? 'border-primary/30 bg-brand-soft text-brand-strong'
                      : 'border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground'
                }`}
              >
                <Settings2 className="size-3.5 shrink-0" aria-hidden />
                <span className="max-w-40 truncate">{model || defaultModel || '模型与时间范围'}</span>
                {extras.length > 0 && <span className="hidden min-w-0 truncate sm:inline">· {extras.join(' · ')}</span>}
                {timeInvalid && <span className="shrink-0">· 时间有误</span>}
                <ChevronDown className={`size-3.5 shrink-0 transition-transform ${advanced ? 'rotate-180' : ''}`} aria-hidden />
              </button>

              <div className="ml-auto flex min-w-0 items-center gap-3">
                <p
                  id="start-blocker"
                  aria-live="polite"
                  className={`min-w-0 truncate text-right text-xs ${showBlocker ? 'hidden sm:block' : 'sr-only'}`}
                >
                  <span className="text-amber-700 dark:text-amber-300">{blocker ?? ''}</span>
                  {showBlocker && block?.field && (
                    <button
                      type="button"
                      onClick={goToBlocker}
                      className="ml-1.5 rounded-sm font-medium text-amber-700 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:text-amber-300"
                    >
                      去填写
                    </button>
                  )}
                </p>
                <button
                  type="submit"
                  disabled={!ready}
                  aria-label="开始分析"
                  aria-describedby={blocker ? 'start-blocker' : undefined}
                  title={blocker ?? '开始分析'}
                  className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xs transition-colors hover:bg-primary/90 disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none"
                >
                  {create.isPending ? <Spinner className="border-white/40 border-t-white" /> : <ArrowUp className="size-4" />}
                </button>
              </div>
            </div>
          </form>

          {showBlocker && <p className="mt-2 text-xs text-amber-700 sm:hidden dark:text-amber-300">{blocker}</p>}
          {create.error && (
            <div className="mt-3">
              <ErrorBox error={create.error} />
            </div>
          )}
          <p className="mt-2 text-center text-xs text-muted-foreground">Enter 开始分析 · Shift + Enter 换行</p>

          {!question.trim() && (
            <section aria-labelledby="try-asking" className="mt-10">
              <h3 id="try-asking" className="mb-3 text-[13px] font-medium text-muted-foreground">
                试试这样问
              </h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {SUGGESTIONS.map(({ tag, text, icon: Icon }) => (
                  <button
                    key={text}
                    type="button"
                    onClick={() => {
                      setQuestion(text)
                      document.getElementById('question')?.focus()
                    }}
                    className="group flex items-start gap-3 rounded-xl border border-border bg-card p-4 text-left transition-colors hover:border-primary/40 hover:bg-brand-soft/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:bg-brand-soft group-hover:text-brand-strong">
                      <Icon className="size-4" aria-hidden />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-foreground">{tag}</span>
                      <span className="mt-0.5 block text-[13px] leading-5 text-muted-foreground">{text}</span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { ChatPanel, type AskRequest } from '../components/ChatPanel'
import { ReportView } from '../components/ReportView'
import { SharePanel } from '../components/SharePanel'
import { SourceViewer } from '../components/SourceViewer'
import { TimelineChart } from '../components/Timeline'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Spinner } from '../components/ui'
import { api, scopeKey, type Scope } from '../lib/api'
import { ASSESSMENT, baseName, formatRange, stampParts } from '../lib/format'
import type { Bucket, Meta, SessionDetail, SourceTarget, Timeline } from '../lib/types'

export interface WorkspaceSearch {
  turn?: number
  src?: string
  start?: number
  end?: number
  ev?: string
  tab?: 'chat'
}

export function validateWorkspaceSearch(search: Record<string, unknown>): WorkspaceSearch {
  const num = (v: unknown) => {
    const n = Number(v)
    return Number.isInteger(n) && n > 0 ? n : undefined
  }
  const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)
  return {
    turn: num(search.turn),
    src: str(search.src),
    start: num(search.start),
    end: num(search.end),
    ev: str(search.ev),
    tab: search.tab === 'chat' ? 'chat' : undefined,
  }
}

const BUCKET_CHOICES = [60, 120, 240]

export function Workspace({
  scope,
  search,
  setSearch,
}: {
  scope: Scope
  search: WorkspaceSearch
  setSearch: (next: WorkspaceSearch) => void
}) {
  const client = useQueryClient()
  const [buckets, setBuckets] = useState(120)
  const [selectedBucket, setSelectedBucket] = useState<number | null>(null)
  const [ask, setAsk] = useState<AskRequest | null>(null)
  const [sharing, setSharing] = useState(false)

  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const session = useQuery({ queryKey: [...scopeKey(scope), 'session'], queryFn: () => api.session(scope), retry: false })
  const turns = session.data?.turn_list ?? []
  const turnNumber = search.turn ?? turns.at(-1)?.turn
  const turn = useQuery({
    queryKey: [...scopeKey(scope), 'turn', turnNumber],
    queryFn: () => api.turn(scope, turnNumber!),
    enabled: turnNumber !== undefined,
  })
  const timeline = useQuery({
    queryKey: [...scopeKey(scope), 'timeline', buckets],
    queryFn: () => api.timeline(scope, buckets),
    enabled: session.isSuccess,
    staleTime: 60_000,
  })

  useEffect(() => setSelectedBucket(null), [buckets])

  const target: SourceTarget | null =
    search.src && search.start ? { source: search.src, start: search.start, end: search.end ?? search.start } : null
  const owner = scope.kind === 'owner'
  const canChat = owner && !!meta.data?.can_chat
  const tab = search.tab === 'chat' && canChat ? 'chat' : 'report'

  function openSource(next: SourceTarget) {
    setSearch({ ...search, src: next.source, start: next.start, end: next.end, ev: next.evidenceKey })
  }

  function askAbout(bucket: Bucket, data: Timeline) {
    setSearch({ ...search, tab: 'chat' })
    setAsk({ text: spikeQuestion(bucket, data), nonce: Date.now() })
  }

  if (session.isLoading) {
    return (
      <div className="flex justify-center p-16">
        <Spinner />
      </div>
    )
  }
  if (session.error || !session.data) {
    return (
      <div className="mx-auto max-w-xl p-8">
        <ErrorBox error={session.error ?? '会话不存在'} />
        {owner && (
          <Link to="/" className="mt-4 inline-block text-sm text-sky-700 hover:underline">
            ← 返回会话列表
          </Link>
        )}
      </div>
    )
  }

  const info = session.data
  return (
    <div className="mx-auto max-w-[1680px] space-y-4 px-3 py-4 sm:px-5">
      <SessionHeader
        info={info}
        scope={scope}
        meta={meta.data}
        turnNumber={turnNumber}
        onShare={owner ? () => setSharing(true) : undefined}
      />

      <Card>
        <CardHeader title="错误时间线">
          {timeline.isFetching && <Spinner />}
          {!!timeline.data?.spikes.length && (
            <div className="flex flex-wrap gap-1">
              {timeline.data.spikes.slice(0, 6).map((i) => {
                const b = timeline.data!.buckets[i]
                return (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setSelectedBucket(i)}
                    className={`rounded-md px-1.5 py-0.5 text-xs ring-1 ring-inset ${selectedBucket === i ? 'bg-red-600 text-white ring-red-600' : 'bg-red-50 text-red-700 ring-red-200 hover:bg-red-100 dark:bg-red-950/50 dark:text-red-300 dark:ring-red-900'}`}
                    title="错误尖峰"
                  >
                    ▲ {stampParts(b.start).time} · {b.error}
                  </button>
                )
              })}
            </div>
          )}
          <select
            value={buckets}
            onChange={(e) => setBuckets(Number(e.target.value))}
            className="rounded-md border border-gray-300 bg-white px-1.5 py-0.5 text-xs dark:border-gray-700 dark:bg-gray-900"
            aria-label="时间线精度"
          >
            {BUCKET_CHOICES.map((n) => (
              <option key={n} value={n}>
                约 {n} 格
              </option>
            ))}
          </select>
        </CardHeader>
        {timeline.error && (
          <div className="p-4">
            <ErrorBox error={timeline.error} />
          </div>
        )}
        {timeline.isLoading && <Empty>正在扫描日志…（大文件首次扫描需要一点时间，之后会缓存）</Empty>}
        {timeline.data && (
          <>
            <TimelineChart data={timeline.data} selected={selectedBucket} onSelect={setSelectedBucket} />
            {timeline.data.files.some((f) => f.error) && (
              <div className="px-4 pb-3">
                {timeline.data.files
                  .filter((f) => f.error)
                  .map((f) => (
                    <ErrorBox key={f.source} error={`${f.name}：${f.error}`} />
                  ))}
              </div>
            )}
            {selectedBucket !== null && timeline.data.buckets[selectedBucket] && (
              <BucketDetail
                bucket={timeline.data.buckets[selectedBucket]}
                data={timeline.data}
                canAsk={canChat}
                onOpen={openSource}
                onAsk={() => askAbout(timeline.data!.buckets[selectedBucket], timeline.data!)}
                onClose={() => setSelectedBucket(null)}
              />
            )}
          </>
        )}
      </Card>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card className="min-w-0">
          <div className="flex items-center gap-1 border-b border-gray-100 px-2 pt-2 dark:border-gray-800">
            <TabButton active={tab === 'report'} onClick={() => setSearch({ ...search, tab: undefined })}>
              报告
            </TabButton>
            {canChat && (
              <TabButton active={tab === 'chat'} onClick={() => setSearch({ ...search, tab: 'chat' })}>
                追问
              </TabButton>
            )}
            {owner && meta.data && !meta.data.can_chat && (
              <span className="ml-auto pb-2 pr-2 text-xs text-gray-400">网页续问不可用：服务未配置 OPENAI_API_KEY 或为只读模式</span>
            )}
          </div>
          {tab === 'report' && (
            <>
              <TurnPicker
                turns={info.turn_list}
                current={turnNumber}
                onPick={(n) => setSearch({ ...search, turn: n, ev: undefined })}
              />
              {turns.length === 0 && <Empty>这个会话还没有保存的报告。</Empty>}
              {turn.isLoading && <Empty>加载报告…</Empty>}
              {turn.error && (
                <div className="p-4">
                  <ErrorBox error={turn.error} />
                </div>
              )}
              {turn.data && <ReportView payload={turn.data} activeEvidence={search.ev} onOpen={openSource} />}
            </>
          )}
          {/* 追问面板常驻挂载：切回报告页时对话与进行中的分析不会丢 */}
          {canChat && scope.kind === 'owner' && (
            <div className={tab === 'chat' ? '' : 'hidden'}>
              <ChatPanel
                session={scope.name}
                turns={info.turn_list}
                ask={ask}
                onOpen={openSource}
                onShowTurn={(n) => setSearch({ ...search, turn: n, tab: undefined, ev: undefined })}
                onTurnSaved={(n) => {
                  void client.invalidateQueries({ queryKey: [...scopeKey(scope), 'session'] })
                  void client.invalidateQueries({ queryKey: ['sessions'] })
                  setSearch({ ...search, turn: n, tab: 'chat', ev: undefined })
                }}
              />
            </div>
          )}
        </Card>

        <div className="min-w-0 lg:sticky lg:top-4 lg:h-[calc(100vh-2rem)]">
          <SourceViewer scope={scope} target={target} />
        </div>
      </div>

      {sharing && owner && meta.data && (
        <SharePanel session={scope.name} meta={meta.data} onClose={() => setSharing(false)} />
      )}
    </div>
  )
}

function spikeQuestion(bucket: Bucket, data: Timeline): string {
  const range = formatRange(bucket.start, bucket.end, data.time_only)
  const top = bucket.top[0]
  const where = top ? `，主要是「${top.signature}」（${top.count} 次，首次出现在 ${baseName(top.source)}:${top.line}）` : ''
  const others = bucket.top.length > 1 ? `，另有 ${bucket.top.slice(1).map((t) => `「${t.signature}」×${t.count}`).join('、')}` : ''
  return (
    `请分析 ${range}（${data.timezone}）这段时间的错误尖峰：共 ${bucket.error} 条 ERROR/FATAL${where}${others}。` +
    '这波错误的直接原因和根因是什么？与之前的结论是否一致？'
  )
}

function SessionHeader({
  info,
  scope,
  meta,
  turnNumber,
  onShare,
}: {
  info: SessionDetail
  scope: Scope
  meta?: Meta
  turnNumber?: number
  onShare?: () => void
}) {
  const settings = info.settings as Record<string, string | number | null>
  const range = settings.since || settings.until ? `${settings.since || '开头'} → ${settings.until || '结尾'}` : null
  return (
    <header className="space-y-2">
      {scope.kind === 'share' ? (
        <div className="rounded-md border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300">
          只读分享视图 · 日志内容已脱敏 · 由 log-agent {meta?.version} 提供
        </div>
      ) : (
        <Link to="/" className="text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200">
          ← 全部会话
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h1 className="truncate text-lg font-semibold">{info.title || info.name}</h1>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
            <Badge tone={info.origin === 'analyze' ? 'blue' : 'gray'}>{info.origin === 'analyze' ? '单次分析' : '多轮对话'}</Badge>
            <span className="font-mono">{info.name}</span>
            <span>· {info.turns} 轮</span>
            <span>· {info.model}</span>
            <span>· 时区 {String(settings.timezone || 'UTC')}</span>
            {range && <span>· 范围 {range}</span>}
            {settings.baseline && <span>· 基线 {String(settings.baseline)}</span>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {info.logs.map((log) => (
              <Badge key={log.path} tone={log.exists === false ? 'red' : 'blue'} title={log.path}>
                {log.exists === false ? '缺失 · ' : ''}
                {log.name}
              </Badge>
            ))}
            {info.code.map((code) => (
              <Badge key={code.path} title={code.path}>
                源码 {code.name}
              </Badge>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {turnNumber !== undefined && (
            <>
              <a className="text-sm text-gray-600 hover:underline dark:text-gray-300" href={api.exportUrl(scope, turnNumber, 'markdown')}>
                导出 Markdown
              </a>
              <a className="text-sm text-gray-600 hover:underline dark:text-gray-300" href={api.exportUrl(scope, turnNumber, 'markdown', 'ticket')}>
                工单视图
              </a>
              <a className="text-sm text-gray-600 hover:underline dark:text-gray-300" href={api.exportUrl(scope, turnNumber, 'json')}>
                JSON
              </a>
            </>
          )}
          {onShare && (
            <Button variant="primary" onClick={onShare}>
              分享
            </Button>
          )}
        </div>
      </div>
    </header>
  )
}

function TurnPicker({
  turns,
  current,
  onPick,
}: {
  turns: SessionDetail['turn_list']
  current?: number
  onPick: (n: number) => void
}) {
  const ordered = useMemo(() => [...turns].reverse(), [turns])
  if (turns.length <= 1) return null
  return (
    <div className="flex gap-1.5 overflow-x-auto border-b border-gray-100 px-3 py-2 dark:border-gray-800">
      {ordered.map((t) => (
        <button
          key={t.turn}
          type="button"
          onClick={() => onPick(t.turn)}
          title={t.question}
          className={`flex max-w-56 shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs ring-1 ring-inset ${
            t.turn === current
              ? 'bg-gray-900 text-white ring-gray-900 dark:bg-gray-100 dark:text-gray-900'
              : 'text-gray-600 ring-gray-200 hover:bg-gray-50 dark:text-gray-300 dark:ring-gray-700 dark:hover:bg-gray-800'
          }`}
        >
          <span className="font-semibold">#{t.turn}</span>
          {t.assessment && (
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                ASSESSMENT[t.assessment].tone === 'red' ? 'bg-red-500' : ASSESSMENT[t.assessment].tone === 'green' ? 'bg-emerald-500' : 'bg-gray-400'
              }`}
            />
          )}
          <span className="truncate">{t.question}</span>
        </button>
      ))}
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-mb-px border-b-2 px-3 pb-2 pt-1 text-sm font-medium ${
        active
          ? 'border-gray-900 text-gray-900 dark:border-gray-100 dark:text-gray-50'
          : 'border-transparent text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'
      }`}
    >
      {children}
    </button>
  )
}

function BucketDetail({
  bucket,
  data,
  canAsk,
  onOpen,
  onAsk,
  onClose,
}: {
  bucket: Bucket
  data: Timeline
  canAsk: boolean
  onOpen: (target: SourceTarget) => void
  onAsk: () => void
  onClose: () => void
}) {
  const firstError = bucket.first_error[0]
  const first = bucket.first[0]
  return (
    <div className="space-y-2 border-t border-gray-100 px-4 py-3 dark:border-gray-800">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{formatRange(bucket.start, bucket.end, data.time_only)}</span>
          {bucket.spike && <Badge tone="red">错误尖峰</Badge>}
          <span className="text-gray-500">
            ERROR {bucket.error} · WARN {bucket.warn} · 共 {bucket.total}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {firstError ? (
            <Button onClick={() => onOpen({ source: firstError.source, start: firstError.line, end: firstError.line })}>
              跳到第一条错误
            </Button>
          ) : (
            first && (
              <Button onClick={() => onOpen({ source: first.source, start: first.line, end: first.line })}>跳到该时段日志</Button>
            )
          )}
          {canAsk && bucket.error > 0 && (
            <Button variant="primary" onClick={onAsk}>
              追问这个时段
            </Button>
          )}
          <Button variant="ghost" onClick={onClose} aria-label="关闭">
            ✕
          </Button>
        </div>
      </div>
      {bucket.top.length > 0 && (
        <ul className="space-y-1">
          {bucket.top.map((t) => (
            <li key={t.signature} className="flex items-center gap-2 text-xs">
              <span className="w-10 shrink-0 text-right font-semibold tabular-nums text-red-600 dark:text-red-400">×{t.count}</span>
              <button
                type="button"
                onClick={() => onOpen({ source: t.source, start: t.line, end: t.line })}
                className="min-w-0 flex-1 truncate text-left font-mono text-gray-700 hover:text-sky-700 hover:underline dark:text-gray-300"
                title="查看首次出现的原文"
              >
                {t.signature}
              </button>
              <span className="shrink-0 font-mono text-gray-400">
                {baseName(t.source)}:{t.line}
              </span>
            </li>
          ))}
        </ul>
      )}
      {bucket.error === 0 && <p className="text-xs text-gray-500">该时段没有 ERROR / FATAL 日志。</p>}
    </div>
  )
}

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { BarChart3, Copy, Download, FileCode2, FileSearch, FileText, FolderCode, GanttChart, Share2, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ChatPanel, type AskRequest } from '../components/ChatPanel'
import { ReportView } from '../components/ReportView'
import { SharePanel } from '../components/SharePanel'
import { SourceViewer } from '../components/SourceViewer'
import { TimelinePane } from '../components/TimelinePane'
import { Badge, Empty, ErrorBox, Spinner } from '../components/ui'
import { api, scopeKey, type Scope } from '../lib/api'
import { ASSESSMENT } from '../lib/format'
import { discardPendingQuestion, holdPendingQuestion } from '../lib/pending'
import type { Meta, SessionDetail, SourceTarget } from '../lib/types'

type Panel = 'report' | 'source' | 'timeline'

export interface WorkspaceSearch {
  turn?: number
  src?: string
  start?: number
  end?: number
  ev?: string
  panel?: Panel
}

export function validateWorkspaceSearch(search: Record<string, unknown>): WorkspaceSearch {
  const num = (v: unknown) => {
    const n = Number(v)
    return Number.isInteger(n) && n > 0 ? n : undefined
  }
  const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)
  const panel = search.panel === 'report' || search.panel === 'source' || search.panel === 'timeline' ? search.panel : undefined
  return {
    turn: num(search.turn),
    src: str(search.src),
    start: num(search.start),
    end: num(search.end),
    ev: str(search.ev),
    panel,
  }
}

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
  const [ask, setAsk] = useState<AskRequest | null>(null)
  const [sharing, setSharing] = useState(false)

  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  // 切回来时总要重新拉：离开期间服务端可能已经跑完并存档了一轮
  const session = useQuery({
    queryKey: [...scopeKey(scope), 'session'],
    queryFn: () => api.session(scope),
    retry: false,
    refetchOnMount: 'always',
  })
  const liveName = scope.kind === 'owner' && meta.data?.can_chat ? scope.name : null
  const live = useQuery({
    queryKey: ['live', liveName],
    queryFn: () => api.liveRun(liveName!),
    enabled: !!liveName,
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: 'always',
    retry: false,
  })
  const turns = session.data?.turn_list ?? []
  const turnNumber = search.turn ?? turns.at(-1)?.turn

  // 对话面板挂不上（会话加载失败 / 不能提问）时，新建页交接过来的第一个问题就作废，
  // 免得以后再打开这个会话时被意外发出去
  const handoffName = scope.kind === 'owner' ? scope.name : null
  const chatUnavailable = !!session.error || (!!meta.data && !meta.data.can_chat)
  useEffect(() => {
    if (handoffName && chatUnavailable) discardPendingQuestion(handoffName)
  }, [handoffName, chatUnavailable])
  // 会话还在加载时一直保留；离开这个会话页（卸载或切到别的会话）时作废
  useEffect(() => (handoffName ? holdPendingQuestion(handoffName) : undefined), [handoffName])

  const target: SourceTarget | null =
    search.src && search.start ? { source: search.src, start: search.start, end: search.end ?? search.start } : null
  const owner = scope.kind === 'owner'
  const canChat = owner && !!meta.data?.can_chat
  // 能提问时主区是对话；只读（分享链接 / 未配置模型）时主区直接是报告
  const panels: Panel[] = canChat ? ['report', 'source', 'timeline'] : ['source', 'timeline']
  const panel = search.panel && panels.includes(search.panel) ? search.panel : undefined

  function openPanel(next: Panel | undefined) {
    setSearch({ ...search, panel: next })
  }

  function openSource(next: SourceTarget) {
    setSearch({ ...search, src: next.source, start: next.start, end: next.end, ev: next.evidenceKey, panel: 'source' })
  }

  if (session.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
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
            返回新建分析
          </Link>
        )}
      </div>
    )
  }

  const info = session.data
  const report = (
    <ReportPane
      scope={scope}
      info={info}
      turnNumber={turnNumber}
      activeEvidence={search.ev}
      onPick={(n) => setSearch({ ...search, turn: n, ev: undefined })}
      onOpen={openSource}
    />
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <SessionHeader
        info={info}
        scope={scope}
        meta={meta.data}
        turnNumber={turnNumber}
        panel={panel}
        panels={panels}
        onPanel={(p) => openPanel(panel === p ? undefined : p)}
        onShare={owner ? () => setSharing(true) : undefined}
      />

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          {/* 聊天面板只用挂载时的历史初始化：等进行中的那一轮和这次重新拉到的会话历史都到了再挂载，
              否则离开期间刚跑完的一轮会缺失 */}
          {canChat && scope.kind === 'owner' && ((live.isPending && !live.isError) || !session.isFetchedAfterMount) ? (
            <div className="flex h-full items-center justify-center">
              <Spinner />
            </div>
          ) : canChat && scope.kind === 'owner' ? (
            <ChatPanel
              key={scope.name}
              session={scope.name}
              turns={info.turn_list}
              live={live.data ?? null}
              ask={ask}
              onOpen={openSource}
              onShowTurn={(n) => setSearch({ ...search, turn: n, panel: 'report', ev: undefined })}
              onTurnSaved={(n) => {
                void client.invalidateQueries({ queryKey: [...scopeKey(scope), 'session'] })
                void client.invalidateQueries({ queryKey: ['sessions'] })
                setSearch({ ...search, turn: n, ev: undefined })
              }}
            />
          ) : (
            <div className="h-full overflow-auto">
              {scope.kind === 'share' && (
                <div className="border-b border-sky-200 bg-sky-50 px-4 py-2 text-xs text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300">
                  只读分享视图 · 日志内容已脱敏 · 由 log-agent {meta.data?.version} 提供
                </div>
              )}
              {owner && meta.data && !meta.data.can_chat && (
                <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
                  网页提问不可用：服务未配置 OPENAI_API_KEY 或为只读模式，仅可查看报告。
                </div>
              )}
              <div className="mx-auto max-w-4xl">{report}</div>
            </div>
          )}
        </div>

        {panel && (
          <aside
            aria-label="详情面板"
            className="fixed inset-0 z-30 flex flex-col bg-white lg:static lg:z-auto lg:w-[min(52%,820px)] lg:shrink-0 lg:border-l lg:border-zinc-200 dark:bg-zinc-950 lg:dark:border-zinc-800"
          >
            <div className="flex items-center gap-1 border-b border-zinc-200 px-2 dark:border-zinc-800">
              {panels.map((p) => (
                <PanelTab key={p} panel={p} active={panel === p} onClick={() => openPanel(p)} />
              ))}
              <button
                type="button"
                onClick={() => openPanel(undefined)}
                aria-label="关闭面板"
                className="ml-auto rounded p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1">
              {panel === 'report' && <div className="h-full overflow-auto">{report}</div>}
              {panel === 'source' && (
                <div className="h-full p-3">
                  <SourceViewer scope={scope} target={target} />
                </div>
              )}
              {panel === 'timeline' && (
                <TimelinePane
                  scope={scope}
                  canAsk={canChat}
                  onOpen={openSource}
                  onAsk={(text) => {
                    setAsk({ text, nonce: Date.now() })
                    openPanel(undefined)
                  }}
                />
              )}
            </div>
          </aside>
        )}
      </div>

      {sharing && owner && meta.data && <SharePanel session={scope.name} meta={meta.data} onClose={() => setSharing(false)} />}
    </div>
  )
}

const PANEL_META: Record<Panel, { label: string; icon: typeof FileText }> = {
  report: { label: '报告', icon: FileSearch },
  source: { label: '原文', icon: FileCode2 },
  timeline: { label: '时间线', icon: BarChart3 },
}

function PanelTab({ panel, active, onClick }: { panel: Panel; active: boolean; onClick: () => void }) {
  const { label, icon: Icon } = PANEL_META[panel]
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium ${
        active
          ? 'border-zinc-900 text-zinc-900 dark:border-zinc-100 dark:text-zinc-50'
          : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
      }`}
    >
      <Icon className="h-4 w-4" aria-hidden />
      {label}
    </button>
  )
}

function HeaderButton({ active, onClick, children, label }: { active?: boolean; onClick?: () => void; children: ReactNode; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={label}
      className={`flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm ${
        active
          ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900'
          : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'
      }`}
    >
      {children}
      <span className="hidden xl:inline">{label}</span>
    </button>
  )
}

function SessionHeader({
  info,
  scope,
  meta,
  turnNumber,
  panel,
  panels,
  onPanel,
  onShare,
}: {
  info: SessionDetail
  scope: Scope
  meta?: Meta
  turnNumber?: number
  panel?: Panel
  panels: Panel[]
  onPanel: (panel: Panel) => void
  onShare?: () => void
}) {
  const [exportOpen, setExportOpen] = useState(false)
  const settings = info.settings as Record<string, string | number | null>
  const range = settings.since || settings.until ? `${settings.since || '开头'} → ${settings.until || '结尾'}` : null
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-800">
      <div className="min-w-0 flex-1 space-y-1">
        <h1 className="truncate text-sm font-semibold">{info.title || '新的分析'}</h1>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
          {info.logs.map((log) => (
            <span
              key={log.path}
              title={log.path}
              className={`flex items-center gap-1 rounded px-1.5 py-0.5 ${
                log.exists === false ? 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300' : 'bg-zinc-100 dark:bg-zinc-800'
              }`}
            >
              <FileText className="h-3 w-3" aria-hidden />
              {log.exists === false ? '缺失 · ' : ''}
              {log.name}
            </span>
          ))}
          {info.code.map((code) => (
            <span key={code.path} title={code.path} className="flex items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">
              <FolderCode className="h-3 w-3" aria-hidden />
              {code.name}
            </span>
          ))}
          <span className="hidden sm:inline">· {info.model}</span>
          {range && <span className="hidden md:inline">· {range}</span>}
          {settings.timezone && <span className="hidden md:inline">· {String(settings.timezone)}</span>}
        </div>
      </div>

      <div className="flex items-center gap-1">
        {panels.map((p) => {
          const { label, icon: Icon } = PANEL_META[p]
          return (
            <HeaderButton key={p} label={label} active={panel === p} onClick={() => onPanel(p)}>
              <Icon className="h-4 w-4" aria-hidden />
            </HeaderButton>
          )
        })}
        {turnNumber !== undefined && (
          <div className="relative">
            <HeaderButton label="导出" active={exportOpen} onClick={() => setExportOpen((v) => !v)}>
              <Download className="h-4 w-4" aria-hidden />
            </HeaderButton>
            {exportOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full z-20 mt-1 w-44 overflow-hidden rounded-lg border border-zinc-200 bg-white py-1 text-sm shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
                onMouseLeave={() => setExportOpen(false)}
              >
                <p className="px-3 py-1 text-xs text-zinc-400">第 {turnNumber} 轮</p>
                {(
                  [
                    ['Markdown 报告', 'markdown', 'detailed'],
                    ['工单视图', 'markdown', 'ticket'],
                    ['JSON', 'json', 'detailed'],
                  ] as const
                ).map(([label, format, view]) => (
                  <a
                    key={label}
                    role="menuitem"
                    href={api.exportUrl(scope, turnNumber, format, view)}
                    className="block px-3 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                  >
                    {label}
                  </a>
                ))}
              </div>
            )}
          </div>
        )}
        {scope.kind === 'owner' && (
          <Link
            to="/trace"
            search={{ session: scope.name }}
            title="查看这个会话每轮的模型、耗时与工具调用"
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            <GanttChart className="h-4 w-4" aria-hidden />
            <span className="hidden xl:inline">Trace</span>
          </Link>
        )}
        {scope.kind === 'owner' && meta?.can_chat && (
          <Link
            to="/"
            search={{ from: scope.name }}
            title="用同样的日志与源码新建分析"
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            <Copy className="h-4 w-4" aria-hidden />
            <span className="hidden xl:inline">复用来源</span>
          </Link>
        )}
        {onShare && (
          <button
            type="button"
            onClick={onShare}
            className="ml-1 flex items-center gap-1.5 rounded-md bg-zinc-900 px-2.5 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
          >
            <Share2 className="h-4 w-4" aria-hidden />
            <span className="hidden sm:inline">分享</span>
          </button>
        )}
      </div>
    </header>
  )
}

function ReportPane({
  scope,
  info,
  turnNumber,
  activeEvidence,
  onPick,
  onOpen,
}: {
  scope: Scope
  info: SessionDetail
  turnNumber?: number
  activeEvidence?: string
  onPick: (n: number) => void
  onOpen: (target: SourceTarget) => void
}) {
  const turn = useQuery({
    queryKey: [...scopeKey(scope), 'turn', turnNumber],
    queryFn: () => api.turn(scope, turnNumber!),
    enabled: turnNumber !== undefined,
  })
  const ordered = useMemo(() => [...info.turn_list].reverse(), [info.turn_list])
  return (
    <div>
      {info.turn_list.length > 1 && (
        <div className="flex gap-1.5 overflow-x-auto border-b border-zinc-100 px-3 py-2 dark:border-zinc-800">
          {ordered.map((t) => (
            <button
              key={t.turn}
              type="button"
              onClick={() => onPick(t.turn)}
              title={t.question}
              className={`flex max-w-56 shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs ring-1 ring-inset ${
                t.turn === turnNumber
                  ? 'bg-zinc-900 text-white ring-zinc-900 dark:bg-zinc-100 dark:text-zinc-900'
                  : 'text-zinc-600 ring-zinc-200 hover:bg-zinc-50 dark:text-zinc-300 dark:ring-zinc-700 dark:hover:bg-zinc-800'
              }`}
            >
              <span className="font-semibold">#{t.turn}</span>
              {t.assessment && <Badge tone={ASSESSMENT[t.assessment].tone}>{ASSESSMENT[t.assessment].label}</Badge>}
              <span className="truncate">{t.question}</span>
            </button>
          ))}
        </div>
      )}
      {info.turn_list.length === 0 && <Empty>还没有报告。提出第一个问题后，结构化报告与证据核对会出现在这里。</Empty>}
      {turn.isLoading && <Empty>加载报告…</Empty>}
      {turn.error && (
        <div className="p-4">
          <ErrorBox error={turn.error} />
        </div>
      )}
      {turn.data && <ReportView payload={turn.data} activeEvidence={activeEvidence} onOpen={onOpen} />}
    </div>
  )
}

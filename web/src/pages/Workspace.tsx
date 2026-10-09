import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { BarChart3, Copy, Download, FileCode2, FileSearch, FileText, FolderCode, GanttChart, Share2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ChatPanel, type AskRequest } from '../components/ChatPanel'
import { ReportView } from '../components/ReportView'
import { SharePanel } from '../components/SharePanel'
import { SourceViewer } from '../components/SourceViewer'
import { TimelinePane } from '../components/TimelinePane'
import { Badge, Empty, ErrorBox, PageSkeleton } from '../components/ui'
import { api, scopeKey, type Scope } from '../lib/api'
import { ASSESSMENT } from '../lib/format'
import { useClickOutside, useMediaQuery, useModal } from '../lib/hooks'
import { discardPendingQuestion, holdPendingQuestion } from '../lib/pending'
import type { Meta, SessionDetail, SourceTarget } from '../lib/types'
import type { Panel, WorkspaceSearch } from '../lib/workspace-search'

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

  // 小屏上面板是全屏覆盖层：当成模态框处理（焦点移入、Tab 循环）；大屏是并排的侧栏，只响应 Esc
  const aside = useRef<HTMLElement>(null)
  const overlayPanel = !useMediaQuery('(min-width: 1024px)')
  const closePanel = useCallback(() => setSearch({ ...search, panel: undefined }), [search, setSearch])
  const onAsideKey = useModal(aside, closePanel, { trap: overlayPanel, enabled: !!panel && overlayPanel })

  function openSource(next: SourceTarget) {
    setSearch({ ...search, src: next.source, start: next.start, end: next.end, ev: next.evidenceKey, panel: 'source' })
  }

  if (session.isLoading) return <PageSkeleton label="加载会话" />
  if (session.error || !session.data) {
    return (
      <div className="mx-auto max-w-xl p-8">
        <ErrorBox error={session.error ?? '会话不存在'} />
        {owner && (
          <Link to="/" className="mt-4 inline-block text-sm text-brand-700 hover:underline">
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
            <PageSkeleton label="加载对话" />
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
                <div className="border-b border-brand-200 bg-brand-50 px-4 py-2 text-xs text-brand-800 dark:border-brand-900 dark:bg-brand-950/40 dark:text-brand-300">
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
            ref={aside}
            aria-label="详情面板"
            tabIndex={-1}
            onKeyDown={onAsideKey}
            {...(overlayPanel ? { role: 'dialog', 'aria-modal': true } : {})}
            className="fixed inset-0 z-30 flex flex-col bg-paper lg:static lg:z-auto lg:w-[min(52%,820px)] lg:shrink-0 lg:border-l lg:border-zinc-200/70 dark:bg-paper-dark lg:dark:border-zinc-800"
          >
            <div className="flex items-center gap-1 border-b border-zinc-200/70 px-3 py-2 dark:border-zinc-800">
              <div role="group" aria-label="切换面板" className="flex gap-0.5 rounded-xl bg-zinc-100 p-0.5 dark:bg-zinc-900">
                {panels.map((p) => (
                  <PanelTab key={p} panel={p} active={panel === p} onClick={() => openPanel(p)} />
                ))}
              </div>
              <button
                type="button"
                onClick={() => openPanel(undefined)}
                aria-label="关闭面板"
                className="ml-auto rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
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
      className={`flex items-center gap-1.5 rounded-[10px] px-3 py-1.5 text-sm font-medium transition-colors ${
        active
          ? 'bg-white text-zinc-900 shadow-xs dark:bg-zinc-800 dark:text-zinc-50'
          : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
      }`}
    >
      <Icon className="h-4 w-4" aria-hidden />
      {label}
    </button>
  )
}

function HeaderButton({
  active,
  onClick,
  children,
  label,
}: {
  active?: boolean
  onClick?: () => void
  children: ReactNode
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={label}
      className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
        active
          ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/50 dark:text-brand-300'
          : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'
      }`}
    >
      {children}
      <span className="hidden xl:inline">{label}</span>
    </button>
  )
}

const EXPORTS = [
  ['Markdown 报告', 'markdown', 'detailed'],
  ['工单视图', 'markdown', 'ticket'],
  ['JSON', 'json', 'detailed'],
] as const

function ExportMenu({ scope, turnNumber }: { scope: Scope; turnNumber: number }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const items = useRef<(HTMLAnchorElement | null)[]>([])
  const close = useCallback((refocus: boolean) => {
    setOpen(false)
    if (refocus) trigger.current?.focus()
  }, [])
  const closeQuietly = useCallback(() => close(false), [close])
  useClickOutside(root, closeQuietly, open)

  useEffect(() => {
    if (open) items.current[0]?.focus()
  }, [open])

  function onMenuKey(event: KeyboardEvent<HTMLDivElement>) {
    const list = items.current.filter((n): n is HTMLAnchorElement => !!n)
    const index = list.indexOf(document.activeElement as HTMLAnchorElement)
    if (event.key === 'Escape') {
      event.preventDefault()
      close(true)
    } else if (event.key === 'Tab') {
      close(false)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const step = event.key === 'ArrowDown' ? 1 : -1
      list[(index + step + list.length) % list.length]?.focus()
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      list[event.key === 'Home' ? 0 : list.length - 1]?.focus()
    }
  }

  return (
    <div ref={root} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="export-menu"
        title="导出"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && !open) {
            e.preventDefault()
            setOpen(true)
          }
        }}
        className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
          open
            ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/50 dark:text-brand-300'
            : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'
        }`}
      >
        <Download className="h-4 w-4" aria-hidden />
        <span className="hidden xl:inline">导出</span>
      </button>
      {open && (
        <div
          id="export-menu"
          role="menu"
          aria-label={`导出第 ${turnNumber} 轮`}
          onKeyDown={onMenuKey}
          className="absolute right-0 top-full z-20 mt-1.5 w-48 overflow-hidden rounded-xl border border-zinc-200 bg-white p-1 text-sm shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
        >
          <p className="px-2.5 py-1.5 text-xs text-zinc-400" aria-hidden>
            第 {turnNumber} 轮
          </p>
          {EXPORTS.map(([label, format, view], i) => (
            <a
              key={label}
              ref={(node) => {
                items.current[i] = node
              }}
              role="menuitem"
              tabIndex={-1}
              href={api.exportUrl(scope, turnNumber, format, view)}
              onClick={() => close(false)}
              className="block rounded-lg px-2.5 py-1.5 text-zinc-700 outline-none hover:bg-zinc-100 focus:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:focus:bg-zinc-800"
            >
              {label}
            </a>
          ))}
        </div>
      )}
    </div>
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
  const settings = info.settings as Record<string, string | number | null>
  const range = settings.since || settings.until ? `${settings.since || '开头'} → ${settings.until || '结尾'}` : null
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-zinc-200/70 px-4 py-3 sm:px-5 dark:border-zinc-800">
      <div className="min-w-0 flex-1 space-y-1.5">
        <h1 className="truncate text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">{info.title || '新的分析'}</h1>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
          {info.logs.map((log) => (
            <span
              key={log.path}
              title={log.path}
              className={`flex items-center gap-1 rounded-full px-2 py-0.5 ring-1 ring-inset ${
                log.exists === false
                  ? 'bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900'
                  : 'bg-white text-zinc-600 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-800'
              }`}
            >
              <FileText className="h-3 w-3" aria-hidden />
              {log.exists === false ? '缺失 · ' : ''}
              {log.name}
            </span>
          ))}
          {info.code.map((code) => (
            <span
              key={code.path}
              title={code.path}
              className="flex items-center gap-1 rounded-full bg-white px-2 py-0.5 text-zinc-600 ring-1 ring-inset ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-800"
            >
              <FolderCode className="h-3 w-3" aria-hidden />
              {code.name}
            </span>
          ))}
          <span className="hidden font-mono sm:inline">{info.model}</span>
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
        {turnNumber !== undefined && <ExportMenu scope={scope} turnNumber={turnNumber} />}
        {scope.kind === 'owner' && (
          <Link
            to="/trace"
            search={{ session: scope.name }}
            title="查看这个会话每轮的模型、耗时与工具调用"
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
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
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            <Copy className="h-4 w-4" aria-hidden />
            <span className="hidden xl:inline">复用来源</span>
          </Link>
        )}
        {onShare && (
          <button
            type="button"
            onClick={onShare}
            className="ml-1.5 flex items-center gap-1.5 rounded-full bg-brand-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-700"
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
        <div className="flex gap-1.5 overflow-x-auto border-b border-zinc-200/70 px-4 py-2.5 dark:border-zinc-800">
          {ordered.map((t) => (
            <button
              key={t.turn}
              type="button"
              onClick={() => onPick(t.turn)}
              title={t.question}
              className={`flex max-w-56 shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-left text-xs ring-1 ring-inset transition-colors ${
                t.turn === turnNumber
                  ? 'bg-brand-50 text-brand-800 ring-brand-200 dark:bg-brand-950/50 dark:text-brand-200 dark:ring-brand-900'
                  : 'bg-white text-zinc-600 ring-zinc-200 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-700 dark:hover:bg-zinc-800'
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

import * as Popover from '@radix-ui/react-popover'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  ArrowLeft,
  BarChart3,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FileCode2,
  FileSearch,
  FileText,
  FolderCode,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Pencil,
  Share2,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from 'react'
import { ChatPanel, type AskRequest } from '../components/ChatPanel'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../components/Menu'
import { MobileNavButton } from '../components/TopBar'
import { ReportView, evidenceTargets } from '../components/ReportView'
import { TurnFeedbackBar } from '../components/TurnFeedbackBar'
import { SessionSourcesDialog } from '../components/SessionSourcesDialog'
import { SharePanel } from '../components/SharePanel'
import { SourceViewer } from '../components/SourceViewer'
import { TimelinePane } from '../components/TimelinePane'
import { useToast } from '../components/Feedback'
import { Badge, buttonClass, Empty, ErrorBox, PageSkeleton } from '../components/ui'
import { api, scopeKey, type Scope } from '../lib/api'
import { ASSESSMENT, formatGenerated, shortModel } from '../lib/format'
import { useMediaQuery, useModal } from '../lib/hooks'
import { discardPendingQuestion, holdPendingQuestion } from '../lib/pending'
import { TimeJumpContext } from '../lib/time-jump'
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
  const [editingSources, setEditingSources] = useState(false)

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
  // 和报告视图同一个查询键，共用缓存；原文面板拿它做证据的上一条 / 下一条
  const turnPayload = useQuery({
    queryKey: [...scopeKey(scope), 'turn', turnNumber],
    queryFn: () => api.turn(scope, turnNumber!),
    enabled: turnNumber !== undefined,
  })
  const evidence = useMemo(() => evidenceTargets(turnPayload.data), [turnPayload.data])

  // 对话面板挂不上（会话加载失败 / 不能提问）时，新建页交接过来的第一个问题就作废，
  // 免得以后再打开这个会话时被意外发出去
  const handoffName = scope.kind === 'owner' ? scope.name : null
  const chatUnavailable = !!session.error || (!!meta.data && !meta.data.can_chat)
  useEffect(() => {
    if (handoffName && chatUnavailable) discardPendingQuestion(handoffName)
  }, [handoffName, chatUnavailable])
  // 会话还在加载时一直保留；离开这个会话页（卸载或切到别的会话）时作废
  useEffect(() => (handoffName ? holdPendingQuestion(handoffName) : undefined), [handoffName])

  // 直接点「原文」没有指定位置时，默认打开本轮第一条证据，而不是一块空面板
  const explicitTarget: SourceTarget | null =
    search.src && search.start ? { source: search.src, start: search.start, end: search.end ?? search.start } : null
  const target = explicitTarget ?? evidence[0] ?? null
  const activeEvidence = explicitTarget ? search.ev : evidence[0]?.evidenceKey
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
  const jumpToTime = useCallback((at: string) => setSearch({ ...search, panel: 'timeline', at }), [search, setSearch])
  const onAsideKey = useModal(aside, closePanel, { trap: overlayPanel, enabled: !!panel && overlayPanel })
  const row = useRef<HTMLDivElement>(null)
  const [panelWidth, startResize, onResizeKey, resetWidth] = usePanelWidth(row)
  const [expanded, setExpanded] = useState(false)

  // 大屏上焦点不在面板里时 Esc 也能关掉侧栏；有菜单 / 弹窗 / 下拉打开时交给它们自己处理
  useEffect(() => {
    if (!panel || overlayPanel) return
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return
      if (document.querySelector('[aria-modal="true"], [role="menu"], [role="listbox"]')) return
      closePanel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [panel, overlayPanel, closePanel])

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
    <TimeJumpContext.Provider value={jumpToTime}>
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
          onEditSources={canChat ? () => setEditingSources(true) : undefined}
        />

        <div ref={row} className="flex min-h-0 flex-1">
          <div className={`min-w-0 flex-1 ${panel && expanded && !overlayPanel ? 'hidden' : ''}`}>
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
                  <div className="border-b border-border bg-brand-soft px-4 py-2 text-xs text-brand-strong">
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
              style={
                !overlayPanel && panelWidth && !expanded
                  ? { width: `clamp(${PANEL_MIN}px, ${panelWidth}px, calc(100% - ${CHAT_MIN}px))` }
                  : undefined
              }
              className={`fixed inset-0 z-30 flex animate-panel-in flex-col bg-background lg:relative lg:inset-auto lg:z-auto lg:shrink-0 lg:border-l lg:border-border ${
                expanded ? 'lg:flex-1 lg:border-l-0' : 'lg:w-[min(52%,820px)]'
              }`}
            >
              {!overlayPanel && !expanded && (
                <div
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="拖动调整面板宽度"
                  aria-valuemin={PANEL_MIN}
                  aria-valuenow={panelWidth ? Math.round(panelWidth) : undefined}
                  tabIndex={0}
                  title="拖动调整宽度，双击恢复默认"
                  onPointerDown={startResize}
                  onKeyDown={onResizeKey}
                  onDoubleClick={resetWidth}
                  className="group absolute inset-y-0 -left-1.5 z-10 flex w-3 cursor-col-resize justify-center outline-none"
                >
                  <span className="h-full w-0.5 rounded-full bg-transparent transition-colors group-hover:bg-primary/60 group-focus-visible:bg-primary group-active:bg-primary" />
                </div>
              )}
              <div className="flex h-12 shrink-0 items-center gap-1 border-b border-border px-3">
                {/* 大屏上标题栏的分段按钮一直可见，这里只放面板名；小屏覆盖层盖住了标题栏，才需要自己的切换 */}
                {overlayPanel ? (
                  <PanelSwitch panels={panels} panel={panel} onPanel={openPanel} compact={false} />
                ) : (
                  <h2 className="px-1.5 text-sm font-semibold text-foreground">{PANEL_META[panel].label}</h2>
                )}
                <div className="ml-auto flex items-center gap-0.5">
                  {!overlayPanel && (
                    <button
                      type="button"
                      onClick={() => setExpanded((v) => !v)}
                      aria-label={expanded ? '还原面板' : '展开面板'}
                      aria-pressed={expanded}
                      title={expanded ? '还原面板' : '展开面板'}
                      className={buttonClass('ghost', 'icon-sm', 'text-muted-foreground')}
                    >
                      {expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => openPanel(undefined)}
                    aria-label="关闭面板"
                    className={buttonClass('ghost', 'icon-sm', 'text-muted-foreground')}
                  >
                    <X className="size-4" />
                  </button>
                </div>
              </div>
              <div className="min-h-0 flex-1">
                {panel === 'report' && <div className="h-full overflow-auto">{report}</div>}
                {panel === 'source' && (
                  <div className="flex h-full flex-col gap-2 p-3">
                    {(panels.includes('report') || evidence.length > 0) && (
                      <EvidenceNav
                        evidence={evidence}
                        active={activeEvidence}
                        onBack={panels.includes('report') ? () => openPanel('report') : undefined}
                        onOpen={openSource}
                      />
                    )}
                    <div className="min-h-0 flex-1">
                      <SourceViewer scope={scope} target={target} />
                    </div>
                  </div>
                )}
                {panel === 'timeline' && (
                  <TimelinePane
                    scope={scope}
                    canAsk={canChat}
                    at={search.at}
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
        {editingSources && canChat && <SessionSourcesDialog info={info} onClose={() => setEditingSources(false)} />}
      </div>
    </TimeJumpContext.Provider>
  )
}

const NAV_BUTTON =
  'flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-card hover:text-foreground disabled:pointer-events-none disabled:opacity-40'

/** 原文面板顶部：回到报告，或在本轮证据之间逐条翻看（[ / ] 快捷键） */
function EvidenceNav({
  evidence,
  active,
  onBack,
  onOpen,
}: {
  evidence: (SourceTarget & { evidenceKey: string })[]
  active?: string
  onBack?: () => void
  onOpen: (target: SourceTarget) => void
}) {
  const index = evidence.findIndex((e) => e.evidenceKey === active)
  const prev = index > 0 ? evidence[index - 1] : undefined
  const next = index < 0 ? evidence[0] : evidence[index + 1]

  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
      const el = event.target as HTMLElement | null
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return
      const target = event.key === '[' ? prev : event.key === ']' ? next : undefined
      if (!target) return
      event.preventDefault()
      onOpen(target)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [prev, next, onOpen])

  const [issue, item] = active?.split('-') ?? []
  return (
    <nav aria-label="证据导航" className="flex items-center gap-1 rounded-lg bg-muted p-1">
      {onBack && (
        <button type="button" onClick={onBack} className={NAV_BUTTON}>
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          返回报告
        </button>
      )}
      {evidence.length > 0 && (
        <div className="ml-auto flex items-center gap-1">
          <span className="px-1 text-xs tabular-nums text-muted-foreground" aria-live="polite">
            {index >= 0 ? `问题 ${issue} · 证据 ${item}（${index + 1}/${evidence.length}）` : `共 ${evidence.length} 条证据`}
          </span>
          <button
            type="button"
            onClick={() => prev && onOpen(prev)}
            disabled={!prev}
            aria-label="上一条证据"
            title="上一条证据 ["
            className={NAV_BUTTON}
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => next && onOpen(next)}
            disabled={!next}
            aria-label={index < 0 ? '查看第一条证据' : '下一条证据'}
            title={index < 0 ? '查看第一条证据 ]' : '下一条证据 ]'}
            className={NAV_BUTTON}
          >
            {index < 0 && '从第一条看'}
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      )}
    </nav>
  )
}

const PANEL_MIN = 320
const CHAT_MIN = 360
const PANEL_WIDTH_KEY = 'log-agent:panel-width'

function readPanelWidth(): number | null {
  try {
    const value = Number(localStorage.getItem(PANEL_WIDTH_KEY))
    return Number.isFinite(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

function savePanelWidth(width: number | null) {
  try {
    if (width === null) localStorage.removeItem(PANEL_WIDTH_KEY)
    else localStorage.setItem(PANEL_WIDTH_KEY, String(Math.round(width)))
  } catch {
    // 隐私模式下写不了本地存储，只是不记住宽度
  }
}

/** 大屏右侧面板的宽度：拖分隔条或用方向键调整，记在本地；null 表示用默认宽度 */
function usePanelWidth(row: RefObject<HTMLDivElement | null>) {
  const [width, setWidth] = useState(readPanelWidth)

  const clamp = useCallback(
    (value: number) => {
      const total = row.current?.getBoundingClientRect().width ?? Infinity
      return Math.max(PANEL_MIN, Math.min(value, total - CHAT_MIN))
    },
    [row],
  )

  function start(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !row.current) return
    event.preventDefault()
    const right = row.current.getBoundingClientRect().right
    let latest = width
    const move = (e: PointerEvent) => {
      latest = clamp(right - e.clientX)
      setWidth(latest)
    }
    const end = () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', end)
      document.removeEventListener('pointercancel', end)
      document.body.style.removeProperty('cursor')
      document.body.style.removeProperty('user-select')
      savePanelWidth(latest)
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', end)
    document.addEventListener('pointercancel', end)
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 96 : 32
    const current = (event.currentTarget.parentElement as HTMLElement | null)?.offsetWidth ?? width ?? PANEL_MIN
    let next: number | null = null
    if (event.key === 'ArrowLeft') next = clamp(current + step)
    else if (event.key === 'ArrowRight') next = clamp(current - step)
    else if (event.key === 'Home') next = PANEL_MIN
    else if (event.key === 'End') next = clamp(Infinity)
    if (next === null) return
    event.preventDefault()
    setWidth(next)
    savePanelWidth(next)
  }

  function reset() {
    setWidth(null)
    savePanelWidth(null)
  }

  return [width, start, onKey, reset] as const
}

const PANEL_META: Record<Panel, { label: string; icon: typeof FileText }> = {
  report: { label: '报告', icon: FileSearch },
  source: { label: '原文', icon: FileCode2 },
  timeline: { label: '时间线', icon: BarChart3 },
}

function PanelSwitch({
  panels,
  panel,
  onPanel,
  className = '',
  compact = true,
}: {
  panels: Panel[]
  panel?: Panel
  onPanel: (panel: Panel) => void
  className?: string
  /** 中等宽度只显示图标，xl 以上才显示文字 */
  compact?: boolean
}) {
  return (
    <div role="group" aria-label="右侧面板" className={`inline-flex items-center rounded-full bg-muted p-0.5 ${className}`}>
      {panels.map((p) => {
        const { label, icon: Icon } = PANEL_META[p]
        const active = panel === p
        return (
          <button
            key={p}
            type="button"
            onClick={() => onPanel(p)}
            aria-pressed={active}
            title={label}
            className={`inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
              active ? 'bg-card font-medium text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <Icon className="size-3.5" aria-hidden />
            <span className={compact ? 'hidden xl:inline' : ''}>{label}</span>
            {compact && <span className="sr-only xl:hidden">{label}</span>}
          </button>
        )
      })}
    </div>
  )
}

const EXPORTS = [
  ['Markdown 报告', 'markdown', 'detailed'],
  ['工单视图', 'markdown', 'ticket'],
  ['JSON', 'json', 'detailed'],
] as const

/** 导出与低频跳转收进一个菜单，标题栏只留高频的面板切换和分享 */
function MoreMenu({
  scope,
  turnNumber,
  canChat,
  onEditSources,
}: {
  scope: Scope
  turnNumber?: number
  canChat: boolean
  onEditSources?: () => void
}) {
  const toast = useToast()
  const owner = scope.kind === 'owner'
  if (turnNumber === undefined && !owner) return null

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="更多操作"
        title="导出、执行记录与复用来源"
        className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:bg-accent data-[state=open]:text-foreground"
      >
        <MoreHorizontal className="size-4" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {onEditSources && (
          <>
            <DropdownMenuItem onSelect={onEditSources}>
              <SlidersHorizontal />
              来源与设置
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}
        {turnNumber !== undefined && (
          <>
            <DropdownMenuLabel>导出第 {turnNumber} 轮</DropdownMenuLabel>
            {EXPORTS.map(([label, format, view]) => (
              <DropdownMenuItem key={label} asChild>
                <a
                  href={api.exportUrl(scope, turnNumber, format, view)}
                  onClick={() => toast(`正在导出第 ${turnNumber} 轮 · ${label}`, 'info')}
                >
                  <Download />
                  {label}
                </a>
              </DropdownMenuItem>
            ))}
          </>
        )}
        {owner && (
          <>
            {turnNumber !== undefined && <DropdownMenuSeparator />}
            <DropdownMenuItem asChild>
              <Link to="/trace" search={{ session: scope.name }}>
                <Activity />
                <span className="min-w-0 flex-1">
                  执行记录
                  <span className="block text-xs text-muted-foreground">每轮的模型、耗时与工具调用</span>
                </span>
              </Link>
            </DropdownMenuItem>
            {canChat && (
              <DropdownMenuItem asChild>
                <Link to="/" search={{ from: scope.name }}>
                  <Copy />
                  <span className="min-w-0 flex-1">
                    复用来源
                    <span className="block text-xs text-muted-foreground">用同样的日志与源码新建分析</span>
                  </span>
                </Link>
              </DropdownMenuItem>
            )}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** 标题栏右侧的「N 个来源 · 时间范围」：点开看这次分析用的数据与设置 */
function SessionInfoPopover({ info, onEditSources }: { info: SessionDetail; onEditSources?: () => void }) {
  const [open, setOpen] = useState(false)
  const settings = info.settings as Record<string, string | number | null>
  const range = settings.since || settings.until ? `${settings.since || '开头'} → ${settings.until || '结尾'}` : null
  const sources = [
    ...info.logs.map((ref) => ({ ...ref, kind: 'log' as const })),
    ...info.code.map((ref) => ({ ...ref, kind: 'code' as const })),
  ]
  const missing = info.logs.some((log) => log.exists === false)
  const rows = [
    { label: '模型', value: info.model || '默认模型' },
    { label: '时间范围', value: range ?? '整份日志' },
    { label: '时区', value: settings.timezone ? String(settings.timezone) : 'UTC' },
    { label: '基线', value: settings.baseline ? String(settings.baseline) : '不对比' },
    { label: '编码', value: settings.encoding ? String(settings.encoding).toUpperCase() : '自动识别' },
  ]

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        className={`inline-flex h-8 max-w-64 items-center gap-1.5 rounded-full border bg-background px-3 text-xs transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
          missing
            ? 'border-red-200 text-red-700 dark:border-red-500/30 dark:text-red-300'
            : 'border-border text-muted-foreground hover:text-foreground'
        }`}
      >
        <span className={`shrink-0 font-medium ${missing ? '' : 'text-foreground'}`}>
          {sources.length} 个来源{missing ? ' · 有缺失' : ''}
        </span>
        <span aria-hidden className="hidden lg:inline">
          ·
        </span>
        <span className="hidden truncate font-mono lg:inline">{range ?? shortModel(info.model)}</span>
        <ChevronDown className="size-3.5 shrink-0" aria-hidden />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          collisionPadding={8}
          className="z-[60] w-80 origin-[var(--radix-popover-content-transform-origin)] rounded-xl border border-border bg-popover text-popover-foreground shadow-lg outline-none data-[state=open]:animate-menu-in"
        >
          <div className="border-b border-border px-4 py-3">
            <Popover.Title className="text-sm font-semibold">会话信息</Popover.Title>
            <Popover.Description className="text-xs text-muted-foreground">这次分析使用的数据和设置</Popover.Description>
          </div>
          <div className="max-h-56 overflow-y-auto px-4 py-3">
            <h3 className="mb-2 text-xs font-medium text-muted-foreground">数据来源</h3>
            <ul className="space-y-2">
              {sources.map((s) => {
                const lost = s.exists === false
                return (
                  <li key={`${s.kind}-${s.path}`} className="flex items-center gap-2.5">
                    <span
                      className={`flex size-7 shrink-0 items-center justify-center rounded-md ${
                        lost ? 'bg-red-50 text-red-600 dark:bg-red-500/15 dark:text-red-300' : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {s.kind === 'code' ? <FolderCode className="size-3.5" /> : <FileText className="size-3.5" />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[13px]" title={s.path}>
                        {s.name}
                      </span>
                      <span className={`block truncate text-xs ${lost ? 'text-red-600 dark:text-red-300' : 'text-muted-foreground'}`}>
                        {lost ? '文件已不存在' : s.kind === 'code' ? '源码目录' : '日志文件'}
                      </span>
                    </span>
                  </li>
                )
              })}
            </ul>
          </div>
          <dl className="grid grid-cols-[72px_1fr] gap-x-3 gap-y-1.5 border-t border-border px-4 py-3 text-[13px]">
            {rows.map((r) => (
              <Fragment key={r.label}>
                <dt className="text-muted-foreground">{r.label}</dt>
                <dd className="truncate" title={r.value}>
                  {r.value}
                </dd>
              </Fragment>
            ))}
          </dl>
          {info.pending_change && (
            <p className="border-t border-border px-4 py-2 text-xs text-amber-700 dark:text-amber-300">设置已改，下一次提问生效</p>
          )}
          {onEditSources && (
            <div className="border-t border-border p-2">
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  onEditSources()
                }}
                className={buttonClass('ghost', 'sm', 'w-full justify-start gap-2')}
              >
                <SlidersHorizontal className="size-4" aria-hidden />
                调整来源与设置
              </button>
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

const TITLE_MAX = 80

/** 会话标题：双击或点铅笔就地改名，Enter 保存、Esc 取消；和侧栏的重命名走同一个接口 */
function TitleEditor({ name, title }: { name: string; title: string }) {
  const client = useQueryClient()
  const toast = useToast()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(title)
  const input = useRef<HTMLInputElement>(null)
  const done = useRef(false)

  function start() {
    done.current = false
    setValue(title)
    setEditing(true)
    requestAnimationFrame(() => input.current?.select())
  }

  async function finish(save: boolean) {
    if (done.current) return
    done.current = true
    setEditing(false)
    const next = value.replace(/\s+/g, ' ').trim()
    if (!save || next === title) return
    if (!next) {
      toast('标题不能为空，已保留原标题', 'error')
      return
    }
    try {
      await api.patchSession(name, { title: next })
      void client.invalidateQueries({ queryKey: ['owner', name, 'session'] })
      void client.invalidateQueries({ queryKey: ['sessions'] })
    } catch (error) {
      toast(`重命名失败：${error instanceof Error ? error.message : String(error)}`, 'error')
    }
  }

  if (editing) {
    const left = TITLE_MAX - value.length
    return (
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <input
          ref={input}
          autoFocus
          value={value}
          maxLength={TITLE_MAX}
          onChange={(e) => setValue(e.target.value)}
          onBlur={() => void finish(true)}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing || e.keyCode === 229) return
            if (e.key === 'Enter') {
              e.preventDefault()
              void finish(true)
            } else if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              void finish(false)
            }
          }}
          aria-label="会话标题"
          className="h-8 w-full max-w-md rounded-md border border-input bg-background px-2 font-heading text-[15px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        />
        {left <= 20 && (
          <span className={`shrink-0 text-xs tabular-nums ${left <= 5 ? 'text-amber-600' : 'text-muted-foreground'}`}>{left}</span>
        )}
      </div>
    )
  }
  return (
    <div className="group flex min-w-0 items-center gap-1">
      <h1
        onDoubleClick={start}
        title="双击重命名"
        className="min-w-0 truncate font-heading text-[15px] font-semibold tracking-tight text-foreground"
      >
        {title || '新的分析'}
      </h1>
      <button
        type="button"
        onClick={start}
        aria-label="重命名会话"
        title="重命名"
        className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground opacity-70 transition-opacity hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
      >
        <Pencil className="size-3.5" aria-hidden />
      </button>
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
  onEditSources,
}: {
  info: SessionDetail
  scope: Scope
  meta?: Meta
  turnNumber?: number
  panel?: Panel
  panels: Panel[]
  onPanel: (panel: Panel) => void
  onShare?: () => void
  onEditSources?: () => void
}) {
  const latest = info.turn_list.at(-1)
  const assessment = latest?.assessment ? ASSESSMENT[latest.assessment] : null

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3 sm:px-5">
      <MobileNavButton />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {scope.kind === 'owner' ? (
          <TitleEditor name={scope.name} title={info.title} />
        ) : (
          <h1 className="min-w-0 truncate font-heading text-[15px] font-semibold tracking-tight text-foreground">
            {info.title || '新的分析'}
          </h1>
        )}
        {assessment && (
          <span className="hidden shrink-0 lg:inline-flex">
            <Badge tone={assessment.tone}>{assessment.label}</Badge>
          </span>
        )}
        {info.pending_change && (
          <span className="hidden shrink-0 xl:inline-flex">
            <Badge tone="amber">设置已改</Badge>
          </span>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
        {info.logs.length + info.code.length > 0 && (
          <div className="hidden md:block">
            <SessionInfoPopover info={info} onEditSources={onEditSources} />
          </div>
        )}
        <PanelSwitch panels={panels} panel={panel} onPanel={onPanel} />
        <MoreMenu scope={scope} turnNumber={turnNumber} canChat={!!meta?.can_chat} onEditSources={onEditSources} />
        {onShare && (
          <button type="button" onClick={onShare} className={buttonClass('primary', 'sm', 'rounded-full')}>
            <Share2 className="size-3.5" aria-hidden />
            <span className="hidden sm:inline">分享</span>
            <span className="sr-only sm:hidden">分享</span>
          </button>
        )}
      </div>
    </header>
  )
}

const TURN_DOT: Record<string, string> = {
  red: 'bg-red-500',
  green: 'bg-emerald-500',
  amber: 'bg-amber-500',
  gray: 'bg-muted-foreground/50',
  blue: 'bg-primary',
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
  const queryClient = useQueryClient()
  const ordered = useMemo(() => [...info.turn_list].reverse(), [info.turn_list])
  const current = info.turn_list.find((t) => t.turn === turnNumber)
  return (
    <div>
      {info.turn_list.length > 1 && (
        <div className="border-b border-border">
          <div role="group" aria-label="切换轮次" className="flex gap-1 overflow-x-auto px-4 py-2.5">
            {ordered.map((t) => (
              <button
                key={t.turn}
                type="button"
                onClick={() => onPick(t.turn)}
                aria-pressed={t.turn === turnNumber}
                title={[
                  `第 ${t.turn} 轮：${t.question}`,
                  t.summary ? `结论：${t.summary}` : null,
                  t.issues ? `${t.issues} 个问题` : null,
                  t.generated_at ? formatGenerated(t.generated_at) : null,
                ]
                  .filter(Boolean)
                  .join('\n')}
                className={`flex h-7 max-w-56 shrink-0 items-center gap-1.5 rounded-full px-3 text-left text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  t.turn === turnNumber
                    ? 'bg-foreground font-medium text-background'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                }`}
              >
                <span className="shrink-0">第 {t.turn} 轮</span>
                {t.assessment && (
                  <span
                    className={`size-1.5 shrink-0 rounded-full ${TURN_DOT[ASSESSMENT[t.assessment].tone]}`}
                    aria-label={ASSESSMENT[t.assessment].label}
                  />
                )}
              </button>
            ))}
          </div>
          {current && turnNumber !== info.turn_list.at(-1)?.turn && (
            <p className="px-4 pb-2.5 text-xs text-amber-700 dark:text-amber-400">
              正在查看较早的第 {current.turn} 轮{current.generated_at ? `（${formatGenerated(current.generated_at)}）` : ''}
              ，之后的轮次可能已有更新的结论。
            </p>
          )}
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
      {turn.data && current && scope.kind === 'owner' && (
        <TurnFeedbackBar
          key={current.turn}
          session={scope.name}
          turn={current.turn}
          feedback={current.feedback}
          onSaved={() => void queryClient.invalidateQueries({ queryKey: scopeKey(scope) })}
        />
      )}
    </div>
  )
}

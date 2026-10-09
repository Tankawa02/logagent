import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  BarChart3,
  ChevronDown,
  Copy,
  Download,
  FileCode2,
  FileSearch,
  FileText,
  FolderCode,
  GanttChart,
  MoreHorizontal,
  Share2,
  X,
} from 'lucide-react'
import {
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
import { ReportView } from '../components/ReportView'
import { SharePanel } from '../components/SharePanel'
import { SourceViewer } from '../components/SourceViewer'
import { TimelinePane } from '../components/TimelinePane'
import { useToast } from '../components/Feedback'
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
  const row = useRef<HTMLDivElement>(null)
  const [panelWidth, startResize, onResizeKey, resetWidth] = usePanelWidth(row)

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

      <div ref={row} className="flex min-h-0 flex-1">
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
            style={
              !overlayPanel && panelWidth ? { width: `clamp(${PANEL_MIN}px, ${panelWidth}px, calc(100% - ${CHAT_MIN}px))` } : undefined
            }
            className="fixed inset-0 z-30 flex animate-panel-in flex-col bg-paper lg:relative lg:inset-auto lg:z-auto lg:w-[min(52%,820px)] lg:shrink-0 lg:border-l lg:border-zinc-200/70 dark:bg-paper-dark lg:dark:border-zinc-800"
          >
            {!overlayPanel && (
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
                <span className="h-full w-0.5 rounded-full bg-transparent transition-colors group-hover:bg-brand-400 group-focus-visible:bg-brand-500 group-active:bg-brand-500" />
              </div>
            )}
            <div className="flex items-center gap-1 border-b border-zinc-200/70 px-3 py-2 dark:border-zinc-800">
              {/* 大屏上标题栏的分段按钮一直可见，这里只放面板名；小屏覆盖层盖住了标题栏，才需要自己的切换 */}
              {overlayPanel ? (
                <PanelSwitch panels={panels} panel={panel} onPanel={openPanel} />
              ) : (
                <h2 className="flex items-center gap-1.5 px-1.5 text-sm font-medium text-zinc-900 dark:text-zinc-100">
                  {(() => {
                    const { label, icon: Icon } = PANEL_META[panel]
                    return (
                      <>
                        <Icon className="h-4 w-4 text-zinc-400" aria-hidden />
                        {label}
                      </>
                    )
                  })()}
                </h2>
              )}
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

function PanelSwitch({
  panels,
  panel,
  onPanel,
  className = '',
}: {
  panels: Panel[]
  panel?: Panel
  onPanel: (panel: Panel) => void
  className?: string
}) {
  return (
    <div role="group" aria-label="详情面板" className={`flex gap-0.5 rounded-xl bg-zinc-100 p-0.5 dark:bg-zinc-900 ${className}`}>
      {panels.map((p) => (
        <PanelTab key={p} panel={p} active={panel === p} onClick={() => onPanel(p)} />
      ))}
    </div>
  )
}

const EXPORTS = [
  ['Markdown 报告', 'markdown', 'detailed'],
  ['工单视图', 'markdown', 'ticket'],
  ['JSON', 'json', 'detailed'],
] as const

const MENU_ITEM =
  'flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-zinc-700 outline-none hover:bg-zinc-100 focus:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:focus:bg-zinc-800'

/** 导出与低频跳转收进一个菜单，标题栏只留高频的面板切换和分享 */
function MoreMenu({ scope, turnNumber, canChat }: { scope: Scope; turnNumber?: number; canChat: boolean }) {
  const [open, setOpen] = useState(false)
  const toast = useToast()
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const items = useRef<(HTMLAnchorElement | null)[]>([])
  const owner = scope.kind === 'owner'
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

  if (turnNumber === undefined && !owner) return null
  const exportCount = turnNumber !== undefined ? EXPORTS.length : 0
  const register = (i: number) => (node: HTMLAnchorElement | null) => {
    items.current[i] = node
  }

  return (
    <div ref={root} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="more-menu"
        aria-label="更多操作"
        title="导出、执行记录与复用来源"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && !open) {
            e.preventDefault()
            setOpen(true)
          }
        }}
        className={`flex h-8 w-8 items-center justify-center rounded-lg transition-colors ${
          open
            ? 'bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
            : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'
        }`}
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden />
      </button>
      {open && (
        <div
          id="more-menu"
          role="menu"
          aria-label="更多操作"
          onKeyDown={onMenuKey}
          className="absolute right-0 top-full z-20 mt-1.5 w-56 origin-top-right animate-menu-in overflow-hidden rounded-xl border border-zinc-200 bg-white p-1 text-sm shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
        >
          {turnNumber !== undefined && (
            <div role="group" aria-labelledby="more-export-label">
              <p id="more-export-label" className="px-2.5 pb-1 pt-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400">
                导出第 {turnNumber} 轮
              </p>
              {EXPORTS.map(([label, format, view], i) => (
                <a
                  key={label}
                  ref={register(i)}
                  role="menuitem"
                  tabIndex={-1}
                  href={api.exportUrl(scope, turnNumber, format, view)}
                  onClick={() => {
                    close(false)
                    toast(`正在导出第 ${turnNumber} 轮 · ${label}`, 'info')
                  }}
                  className={MENU_ITEM}
                >
                  <Download className="h-4 w-4 text-zinc-400" aria-hidden />
                  {label}
                </a>
              ))}
            </div>
          )}
          {owner && (
            <div
              role="group"
              aria-label="跳转"
              className={turnNumber !== undefined ? 'mt-1 border-t border-zinc-200/80 pt-1 dark:border-zinc-800' : ''}
            >
              <Link
                ref={register(exportCount)}
                role="menuitem"
                tabIndex={-1}
                to="/trace"
                search={{ session: scope.name }}
                onClick={() => close(false)}
                className={MENU_ITEM}
              >
                <GanttChart className="h-4 w-4 text-zinc-400" aria-hidden />
                <span className="min-w-0 flex-1">
                  执行记录
                  <span className="block text-xs text-zinc-500 dark:text-zinc-400">每轮的模型、耗时与工具调用</span>
                </span>
              </Link>
              {canChat && (
                <Link
                  ref={register(exportCount + 1)}
                  role="menuitem"
                  tabIndex={-1}
                  to="/"
                  search={{ from: scope.name }}
                  onClick={() => close(false)}
                  className={MENU_ITEM}
                >
                  <Copy className="h-4 w-4 text-zinc-400" aria-hidden />
                  <span className="min-w-0 flex-1">
                    复用来源
                    <span className="block text-xs text-zinc-500 dark:text-zinc-400">用同样的日志与源码新建分析</span>
                  </span>
                </Link>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function SourceChips({ info }: { info: SessionDetail }) {
  return (
    <>
      {info.logs.map((log) => (
        <span
          key={log.path}
          title={log.path}
          className={`flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 ring-1 ring-inset ${
            log.exists === false
              ? 'bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900'
              : 'bg-white text-zinc-600 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-800'
          }`}
        >
          <FileText className="h-3 w-3 shrink-0" aria-hidden />
          <span className="truncate">
            {log.exists === false ? '缺失 · ' : ''}
            {log.name}
          </span>
        </span>
      ))}
      {info.code.map((code) => (
        <span
          key={code.path}
          title={code.path}
          className="flex max-w-full items-center gap-1 rounded-full bg-white px-2 py-0.5 text-zinc-600 ring-1 ring-inset ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-800"
        >
          <FolderCode className="h-3 w-3 shrink-0" aria-hidden />
          <span className="truncate">{code.name}</span>
        </span>
      ))}
    </>
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
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const settings = info.settings as Record<string, string | number | null>
  const range = settings.since || settings.until ? `${settings.since || '开头'} → ${settings.until || '结尾'}` : null
  const metaText = [info.model, range, settings.timezone ? String(settings.timezone) : null].filter(Boolean).join(' · ')
  const sourceCount = info.logs.length + info.code.length
  const missing = info.logs.some((log) => log.exists === false)

  return (
    <header className="space-y-2.5 border-b border-zinc-200/70 px-4 py-3 sm:px-5 dark:border-zinc-800">
      <div className="flex items-center gap-3">
        <h1 className="line-clamp-2 min-w-0 flex-1 text-[15px] font-semibold leading-snug tracking-tight text-zinc-900 sm:truncate dark:text-zinc-50">
          {info.title || '新的分析'}
        </h1>
        <PanelSwitch panels={panels} panel={panel} onPanel={onPanel} className="hidden shrink-0 md:flex" />
        <div className="flex shrink-0 items-center gap-1">
          <MoreMenu scope={scope} turnNumber={turnNumber} canChat={!!meta?.can_chat} />
          {onShare && (
            <button
              type="button"
              onClick={onShare}
              aria-label="分享"
              className="flex h-8 items-center gap-1.5 rounded-full bg-brand-600 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-700 sm:px-3.5"
            >
              <Share2 className="h-4 w-4" aria-hidden />
              <span className="hidden sm:inline">分享</span>
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-2 text-xs text-zinc-600 dark:text-zinc-400">
        <PanelSwitch panels={panels} panel={panel} onPanel={onPanel} className="md:hidden" />
        {/* 小屏把来源收成一个开关，避免标签挤成两三行 */}
        {sourceCount > 0 && (
          <button
            type="button"
            onClick={() => setSourcesOpen((v) => !v)}
            aria-expanded={sourcesOpen}
            aria-controls="session-sources"
            className={`flex items-center gap-1 rounded-full px-2 py-1 ring-1 ring-inset sm:hidden ${
              missing
                ? 'bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900'
                : 'bg-white text-zinc-600 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-800'
            }`}
          >
            <FileText className="h-3 w-3" aria-hidden />
            {sourceCount} 个来源{missing ? ' · 有缺失' : ''}
            <ChevronDown className={`h-3 w-3 transition-transform ${sourcesOpen ? 'rotate-180' : ''}`} aria-hidden />
          </button>
        )}
        <div id="session-sources" className={`${sourcesOpen ? 'flex' : 'hidden'} w-full flex-wrap gap-1.5 sm:flex sm:w-auto`}>
          <SourceChips info={info} />
        </div>
        {metaText && (
          <span title={metaText} className="hidden min-w-0 max-w-full truncate font-mono text-zinc-500 sm:inline dark:text-zinc-400">
            {metaText}
          </span>
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

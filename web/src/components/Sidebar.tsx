import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import {
  Activity,
  Brain,
  History,
  Keyboard,
  Lightbulb,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Pin,
  PinOff,
  Plus,
  ScanSearch,
  Search,
  Settings,
  Sparkles,
  Trash2,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { api } from '../lib/api'
import { dismissDeleteError, scheduleDelete, undoDelete, useDeferredDelete } from '../lib/deferred-delete'
import { ASSESSMENT, relativeTime } from '../lib/format'
import { MOD } from '../lib/shortcuts'
import type { SessionSummary } from '../lib/types'
import { useToast } from './Feedback'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './Menu'
import { openShortcuts } from './ShortcutsDialog'
import { ThemeCycleButton } from './ThemeToggle'
import { ErrorBox, Skeleton } from './ui'

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function groupLabel(stamp: string): string {
  const day = stamp.slice(0, 10)
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  if (day === fmt(now)) return '今天'
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (day === fmt(yesterday)) return '昨天'
  const week = new Date(now)
  week.setDate(now.getDate() - 7)
  if (day >= fmt(week)) return '最近 7 天'
  return '更早'
}

const DOT: Record<string, string> = {
  red: 'bg-red-500',
  green: 'bg-emerald-500',
  amber: 'bg-amber-500',
  gray: 'bg-muted-foreground/40',
  blue: 'bg-primary',
}

const NAV: { to: '/trace' | '/skills' | '/memory' | '/experience'; icon: LucideIcon; label: string; hint: string }[] = [
  { to: '/trace', icon: Activity, label: '执行记录', hint: 'Trace · 模型、耗时与工具调用' },
  { to: '/skills', icon: Sparkles, label: '技能', hint: 'Skills · 排查手册' },
  { to: '/memory', icon: Brain, label: '记忆', hint: 'Memory · 偏好、术语与事实' },
  { to: '/experience', icon: Lightbulb, label: '经验', hint: 'Experience · 从过往排查提炼的策略' },
]

const NAV_ITEM =
  'flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-sm text-foreground/80 transition-colors hover:bg-sidebar-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const NAV_ACTIVE = 'bg-card font-medium text-foreground shadow-sm ring-1 ring-border hover:bg-card'
const ICON_BUTTON =
  'inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

function LogoMark() {
  return (
    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground" aria-hidden>
      <ScanSearch className="size-[18px]" />
    </span>
  )
}

const NEW_HINT = `新建分析（${MOD}+Shift+O）`

let handledSearchFocus = 0

/** 折叠后的窄栏：只留图标入口 */
export function SidebarRail({ onExpand }: { onExpand: () => void }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  return (
    <nav aria-label="主导航" className="flex h-full flex-col items-center bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 items-center justify-center">
        <Link to="/" aria-label="log-agent 首页">
          <LogoMark />
        </Link>
      </div>
      <div className="flex flex-col items-center gap-1">
        <button type="button" onClick={onExpand} aria-label="展开侧边栏" title="展开侧边栏" className={`${ICON_BUTTON} mb-1`}>
          <PanelLeftOpen className="size-4" />
        </button>
        <Link
          to="/"
          aria-label="新建分析"
          title={NEW_HINT}
          className="inline-flex size-9 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xs transition-colors hover:bg-primary/90"
        >
          <Plus className="size-4" />
        </Link>
      </div>
      <ul className="mt-3 flex flex-col items-center gap-0.5">
        {NAV.map(({ to, icon: Icon, label }) => {
          const active = pathname.startsWith(to)
          return (
            <li key={to}>
              <Link
                to={to}
                title={label}
                aria-label={label}
                className={`${NAV_ITEM} size-9 justify-center px-0 ${active ? NAV_ACTIVE : ''}`}
              >
                <Icon className={`size-4 ${active ? 'text-brand-strong' : 'text-muted-foreground'}`} aria-hidden />
              </Link>
            </li>
          )
        })}
        <li>
          <button
            type="button"
            onClick={onExpand}
            title="历史会话"
            aria-label="历史会话"
            className={`${NAV_ITEM} size-9 justify-center px-0`}
          >
            <History className="size-4 text-muted-foreground" aria-hidden />
          </button>
        </li>
      </ul>
      <div className="flex-1" />
      <div className="flex w-full flex-col items-center gap-1 border-t border-sidebar-border py-2.5">
        <Link
          to="/settings"
          title="设置"
          aria-label="设置"
          className={`${NAV_ITEM} size-9 justify-center px-0 ${pathname.startsWith('/settings') ? NAV_ACTIVE : ''}`}
        >
          <Settings className="size-4 text-muted-foreground" aria-hidden />
        </Link>
        <ThemeCycleButton className={ICON_BUTTON} />
      </div>
    </nav>
  )
}

export function Sidebar({
  onNavigate,
  onCollapse,
  onClose,
  focusSearch = 0,
}: {
  onNavigate?: () => void
  onCollapse?: () => void
  /** 移动端抽屉里显示关闭按钮 */
  onClose?: () => void
  /** 每次 +1 就把焦点放到搜索框（⌘K） */
  focusSearch?: number
}) {
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [searchFocused, setSearchFocused] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const toast = useToast()

  useEffect(() => {
    const input = searchInput.current
    // 桌面侧栏在小屏上仍挂载但被隐藏；只由看得见的那一个处理，也不在之后重新挂载时重复抢焦点
    if (!input || focusSearch <= handledSearchFocus || input.offsetParent === null) return
    handledSearchFocus = focusSearch
    input.focus()
    input.select()
  }, [focusSearch])
  const client = useQueryClient()
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (s) => s.location.pathname })

  useEffect(() => {
    const timer = setTimeout(() => setQ(text.trim()), 250)
    return () => clearTimeout(timer)
  }, [text])

  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const sessions = useQuery({
    queryKey: ['sessions', q],
    queryFn: () => api.sessions(q),
    enabled: !!meta.data?.authenticated,
    placeholderData: keepPreviousData,
  })

  const deletion = useDeferredDelete()

  function requestDelete(s: SessionSummary) {
    const label = s.title || s.name
    scheduleDelete({ name: s.name, label }, async () => {
      // 真正提交时先从缓存里拿掉，免得等接口返回的这段时间它又闪回列表
      client.setQueriesData<SessionSummary[]>({ queryKey: ['sessions'] }, (old) => old?.filter((x) => x.name !== s.name))
      try {
        await api.deleteSession(s.name)
      } finally {
        void client.invalidateQueries({ queryKey: ['sessions'] })
      }
    })
    if (pathname === `/sessions/${encodeURIComponent(s.name)}` || pathname === `/sessions/${s.name}`) void navigate({ to: '/' })
  }

  async function patch(s: SessionSummary, body: { title?: string; pinned?: boolean }) {
    try {
      await api.patchSession(s.name, body)
      void client.invalidateQueries({ queryKey: ['sessions'] })
      if (body.title !== undefined) void client.invalidateQueries({ queryKey: ['owner', s.name, 'session'] })
    } catch (error) {
      toast(`${body.title !== undefined ? '重命名' : body.pinned ? '置顶' : '取消置顶'}失败：${errorText(error)}`, 'error')
    }
  }

  async function rename(s: SessionSummary, title: string) {
    setEditing(null)
    const next = title.trim()
    if (!next || next === s.title) return
    client.setQueriesData<SessionSummary[]>({ queryKey: ['sessions'] }, (old) =>
      old?.map((x) => (x.name === s.name ? { ...x, title: next } : x)),
    )
    await patch(s, { title: next })
  }

  const groups = useMemo(() => {
    const map = new Map<string, SessionSummary[]>()
    for (const s of sessions.data ?? []) {
      if (s.name === deletion.pending?.name) continue
      const label = s.pinned ? '置顶' : groupLabel(s.updated_at)
      map.set(label, [...(map.get(label) ?? []), s])
    }
    // 置顶组永远在最前；其他组按最近更新的先后自然排好
    return [...map.entries()].sort(([a], [b]) => Number(b === '置顶') - Number(a === '置顶'))
  }, [sessions.data, deletion.pending?.name])
  const flat = useMemo(() => groups.flatMap(([, items]) => items), [groups])

  // 搜索框里 ↑ / ↓ 在结果间移动，Enter 打开；换了搜索词就回到第一条
  const [cursor, setCursor] = useState(-1)
  useEffect(() => setCursor(q ? 0 : -1), [q])
  const activeName = searchFocused && cursor >= 0 ? flat[cursor]?.name : undefined

  function openSession(s: SessionSummary, input: HTMLInputElement) {
    input.blur()
    setText('')
    onNavigate?.()
    void navigate({ to: '/sessions/$name', params: { name: s.name } })
  }

  function onSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return
    if (event.key === 'Escape') {
      event.preventDefault()
      setText('')
      event.currentTarget.blur()
    } else if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && flat.length) {
      event.preventDefault()
      const step = event.key === 'ArrowDown' ? 1 : -1
      setCursor((c) => (c < 0 ? (step > 0 ? 0 : flat.length - 1) : (c + step + flat.length) % flat.length))
    } else if (event.key === 'Enter') {
      const target = flat[cursor] ?? (q ? flat[0] : undefined)
      if (!target) return
      event.preventDefault()
      openSession(target, event.currentTarget)
    }
  }

  useEffect(() => {
    if (activeName) document.getElementById(`session-option-${activeName}`)?.scrollIntoView({ block: 'nearest' })
  }, [activeName])

  const canChat = meta.data?.can_chat

  return (
    <nav aria-label="主导航" className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 shrink-0 items-center gap-2 px-3">
        <Link to="/" onClick={onNavigate} className="flex min-w-0 flex-1 items-center gap-2 rounded-lg" aria-label="log-agent 首页">
          <LogoMark />
          <span className="truncate font-heading text-[15px] font-semibold tracking-tight">log-agent</span>
        </Link>
        {onClose ? (
          <button type="button" onClick={onClose} aria-label="关闭导航" className={ICON_BUTTON}>
            <X className="size-4" />
          </button>
        ) : (
          onCollapse && (
            <button type="button" onClick={onCollapse} aria-label="收起侧边栏" title="收起侧边栏" className={ICON_BUTTON}>
              <PanelLeftClose className="size-4" />
            </button>
          )
        )}
      </div>

      <div className="px-3">
        <Link
          to="/"
          onClick={onNavigate}
          title={NEW_HINT}
          className="flex h-9 w-full items-center gap-2 rounded-full bg-primary px-4 text-sm font-medium text-primary-foreground shadow-xs transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40"
        >
          <Plus className="size-4" aria-hidden />
          新建分析
          <kbd className="ml-auto rounded bg-primary-foreground/15 px-1.5 py-0.5 font-mono text-xs" aria-hidden>
            {MOD === '⌘' ? '⌘⇧O' : 'Ctrl⇧O'}
          </kbd>
        </Link>
      </div>

      <ul className="mt-3 space-y-0.5 px-3">
        {NAV.map(({ to, icon: Icon, label, hint }) => {
          const active = pathname.startsWith(to)
          return (
            <li key={to}>
              <Link to={to} onClick={onNavigate} title={hint} className={`${NAV_ITEM} ${active ? NAV_ACTIVE : ''}`}>
                <Icon className={`size-4 shrink-0 ${active ? 'text-brand-strong' : 'text-muted-foreground'}`} aria-hidden />
                {label}
              </Link>
            </li>
          )
        })}
      </ul>

      <div className="mt-4 px-3">
        <label className="relative block">
          <span className="sr-only">搜索会话（↑↓ 选择，Enter 打开）</span>
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input
            ref={searchInput}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onSearchKey}
            onFocus={() => setSearchFocused(true)}
            onBlur={() => setSearchFocused(false)}
            placeholder="搜索会话"
            aria-controls="session-list"
            aria-activedescendant={activeName ? `session-option-${activeName}` : undefined}
            aria-keyshortcuts={MOD === '⌘' ? 'Meta+K' : 'Control+K'}
            className="h-9 w-full rounded-lg border border-input bg-background pl-8 pr-12 text-sm outline-none transition-shadow placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
          />
          {!searchFocused && !text && (
            <kbd
              aria-hidden
              className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-border bg-muted px-1.5 font-mono text-xs text-muted-foreground"
            >
              {MOD}K
            </kbd>
          )}
        </label>
      </div>

      {searchFocused && flat.length > 0 && (
        <p className="mt-1.5 px-4 text-[11px] text-muted-foreground" aria-hidden>
          ↑↓ 选择 · Enter 打开 · Esc 清空
        </p>
      )}
      <div id="session-list" aria-label="会话列表" className="mt-3 min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {sessions.isLoading && (
          <div role="status" aria-label="加载会话列表" className="space-y-3 px-2.5 pt-1">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="space-y-1.5">
                <Skeleton className={`h-3.5 ${i % 2 ? 'w-3/5' : 'w-4/5'}`} />
                <Skeleton className="h-2.5 w-2/5" />
              </div>
            ))}
          </div>
        )}
        {deletion.failed && (
          <div role="alert" className="mb-2 space-y-1">
            <ErrorBox error={`删除会话「${deletion.failed.item.label}」失败：${errorText(deletion.failed.error)}`} />
            <button type="button" onClick={dismissDeleteError} className="px-1 text-xs text-muted-foreground hover:underline">
              知道了
            </button>
          </div>
        )}
        {sessions.error && (
          <div role="alert" className="mb-2 space-y-1">
            <ErrorBox
              error={`${sessions.data ? '刷新会话列表失败，下面是上次加载的结果' : '加载会话列表失败'}：${errorText(sessions.error)}`}
            />
            <button
              type="button"
              onClick={() => void sessions.refetch()}
              disabled={sessions.isFetching}
              className="px-1 text-xs text-brand-strong hover:underline disabled:opacity-60"
            >
              {sessions.isFetching ? '重试中…' : '重试'}
            </button>
          </div>
        )}
        {sessions.data?.length === 0 && (
          <p className="px-2.5 py-6 text-center text-sm leading-relaxed text-muted-foreground">
            {q ? `没有匹配「${q}」的会话` : '还没有会话，点上方「新建分析」开始'}
          </p>
        )}
        {groups.map(([label, items]) => (
          <section key={label} aria-label={label} className="mt-3 first:mt-0">
            <h2 className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">{label}</h2>
            <ul className="space-y-0.5">
              {items.map((s) =>
                editing === s.name ? (
                  <li key={s.name}>
                    <RenameInput initial={s.title} onSave={(title) => void rename(s, title)} onCancel={() => setEditing(null)} />
                  </li>
                ) : (
                  <SessionRow
                    key={s.name}
                    session={s}
                    active={pathname === `/sessions/${encodeURIComponent(s.name)}` || pathname === `/sessions/${s.name}`}
                    highlighted={activeName === s.name}
                    showSummary={!!q}
                    onNavigate={onNavigate}
                    onTogglePin={() => void patch(s, { pinned: !s.pinned })}
                    onRename={() => setEditing(s.name)}
                    onDelete={() => requestDelete(s)}
                  />
                ),
              )}
            </ul>
          </section>
        ))}
      </div>

      <div className="flex items-center gap-1 border-t border-sidebar-border px-3 py-2.5">
        <Link
          to="/settings"
          onClick={onNavigate}
          title="模型连接、API Key 与默认值"
          className={`${NAV_ITEM} w-auto flex-1 ${pathname.startsWith('/settings') ? NAV_ACTIVE : ''}`}
        >
          <Settings className="size-4 text-muted-foreground" aria-hidden />
          设置
        </Link>
        {meta.data && (
          <span
            className="flex items-center gap-1.5 px-1 text-xs text-muted-foreground"
            title={`log-agent v${meta.data.version}${meta.data.db ? ` · ${meta.data.db}` : ''}`}
          >
            <span className={`size-1.5 rounded-full ${canChat ? 'bg-emerald-500' : 'bg-amber-500'}`} aria-hidden />
            {canChat ? '已连接' : '只读'}
          </span>
        )}
        <button type="button" onClick={openShortcuts} title="键盘快捷键（?）" aria-label="键盘快捷键" className={ICON_BUTTON}>
          <Keyboard className="size-4" aria-hidden />
        </button>
        <ThemeCycleButton className={ICON_BUTTON} />
      </div>

      <div className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-4" aria-live="polite">
        {deletion.pending && (
          <div
            role="status"
            className="pointer-events-auto flex animate-toast-in items-center gap-3 rounded-xl bg-foreground py-2 pl-4 pr-2 text-sm text-background shadow-lg motion-reduce:animate-none"
          >
            <span className="max-w-[240px] truncate">已删除「{deletion.pending.label}」</span>
            <button
              type="button"
              onClick={undoDelete}
              className="rounded-md px-2 py-1 font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-background/60"
            >
              撤销
            </button>
          </div>
        )}
      </div>
    </nav>
  )
}

function SessionRow({
  session: s,
  active,
  highlighted,
  showSummary,
  onNavigate,
  onTogglePin,
  onRename,
  onDelete,
}: {
  session: SessionSummary
  active: boolean
  highlighted: boolean
  showSummary: boolean
  onNavigate?: () => void
  onTogglePin: () => void
  onRename: () => void
  onDelete: () => void
}) {
  const renaming = useRef(false)
  const tone = s.last?.assessment ? ASSESSMENT[s.last.assessment].tone : 'gray'
  const label = s.title || '新的分析'
  const status = s.last?.assessment ? ASSESSMENT[s.last.assessment].label : s.turns ? '暂无结论' : '未提问'
  const source = `${s.logs[0]?.name ?? '无日志'}${s.logs.length > 1 ? ` +${s.logs.length - 1}` : ''}${s.origin === 'analyze' ? ' · CLI' : ''}`

  return (
    <li
      className={`group relative flex items-center gap-1 rounded-lg transition-colors ${
        active ? 'bg-card shadow-sm ring-1 ring-border' : 'hover:bg-sidebar-accent'
      } ${highlighted ? 'ring-2 ring-inset ring-primary/60' : ''}`}
    >
      <Link
        id={`session-option-${s.name}`}
        to="/sessions/$name"
        params={{ name: s.name }}
        onClick={onNavigate}
        onDoubleClick={(e) => {
          e.preventDefault()
          onRename()
        }}
        title={s.title || s.name}
        aria-current={active ? 'page' : undefined}
        className="flex min-w-0 flex-1 items-start gap-2.5 rounded-lg px-2.5 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className={`mt-[7px] size-2 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className={`flex items-center gap-1 text-sm ${active ? 'font-medium text-foreground' : 'text-foreground/90'}`}>
            {s.pinned && <Pin className="size-3 shrink-0 text-brand-strong" aria-label="已置顶" />}
            <span className="truncate">{label}</span>
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            <span className="sr-only">{status}，</span>
            {source} ·{' '}
            <time dateTime={s.updated_at} title={s.updated_at.replace('T', ' ').slice(0, 19)} className="tabular-nums">
              {relativeTime(s.updated_at)}
            </time>
          </span>
          {/* 搜索时多给一行最近一轮的结论，方便认出要找的是哪个会话 */}
          {showSummary && s.last?.summary && (
            <span className="mt-0.5 line-clamp-2 text-xs leading-snug text-muted-foreground">{s.last.summary}</span>
          )}
        </span>
      </Link>
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label={`${label} 的更多操作`}
          className={`mr-1 inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:opacity-100 pointer-coarse:opacity-100 ${
            active ? 'opacity-100' : 'opacity-0 group-focus-within:opacity-100 group-hover:opacity-100'
          }`}
        >
          <MoreHorizontal className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="w-40"
          onCloseAutoFocus={(e) => {
            // 重命名时焦点要留给输入框，不能被还给「…」按钮
            if (renaming.current) e.preventDefault()
            renaming.current = false
          }}
        >
          <DropdownMenuItem onSelect={onTogglePin}>
            {s.pinned ? <PinOff /> : <Pin />}
            {s.pinned ? '取消置顶' : '置顶'}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              renaming.current = true
              onRename()
            }}
          >
            <Pencil />
            重命名
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={onDelete}>
            <Trash2 />
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  )
}

function RenameInput({ initial, onSave, onCancel }: { initial: string; onSave: (title: string) => void; onCancel: () => void }) {
  const [value, setValue] = useState(initial)
  const done = useRef(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    // 从下拉菜单触发时菜单关闭会把焦点还给触发按钮，晚一帧再抢回来
    const frame = requestAnimationFrame(() => {
      input.current?.focus()
      input.current?.select()
    })
    return () => cancelAnimationFrame(frame)
  }, [])

  function finish(save: boolean) {
    if (done.current) return
    done.current = true
    if (save) onSave(value)
    else onCancel()
  }

  return (
    <input
      ref={input}
      value={value}
      maxLength={80}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (e.nativeEvent.isComposing || e.keyCode === 229) return
        if (e.key === 'Enter') {
          e.preventDefault()
          finish(true)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          finish(false)
        }
      }}
      aria-label="会话标题"
      placeholder="会话标题"
      className="m-0.5 h-9 w-[calc(100%-4px)] rounded-md border border-input bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
    />
  )
}

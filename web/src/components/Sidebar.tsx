import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import { BookOpen, Brain, GanttChart, PanelLeftClose, PanelLeftOpen, Plus, Search, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { ASSESSMENT } from '../lib/format'
import type { SessionSummary } from '../lib/types'
import { ErrorBox, Spinner } from './ui'

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
  gray: 'bg-zinc-300 dark:bg-zinc-600',
  blue: 'bg-brand-500',
}

const NAV = [
  { to: '/trace', icon: GanttChart, label: 'Trace', hint: '模型、耗时与工具调用' },
  { to: '/skills', icon: BookOpen, label: 'Skills', hint: '排查手册' },
  { to: '/memory', icon: Brain, label: 'Memory', hint: '偏好、术语与事实' },
] as const

const ICON_BUTTON =
  'flex h-9 w-9 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'

function LogoMark({ className = 'h-7 w-7' }: { className?: string }) {
  return (
    <span className={`flex shrink-0 items-center justify-center rounded-lg bg-brand-600 text-white ${className}`} aria-hidden>
      <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor">
        <path d="M3 11.5h2v-4H3zm4 0h2v-7H7zm4 0h2V8h-2z" />
      </svg>
    </span>
  )
}

export function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2 rounded-lg" aria-label="log-agent 首页">
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">log-agent</span>
    </Link>
  )
}

function NewButton({ onNavigate, compact = false }: { onNavigate?: () => void; compact?: boolean }) {
  if (compact) {
    return (
      <Link
        to="/"
        onClick={onNavigate}
        aria-label="新建分析"
        title="新建分析"
        className="flex h-9 w-9 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-700 shadow-xs hover:border-brand-300 hover:text-brand-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
      >
        <Plus className="h-4 w-4" />
      </Link>
    )
  }
  return (
    <Link
      to="/"
      onClick={onNavigate}
      className="group flex w-full items-center gap-2 rounded-full border border-zinc-200 bg-white py-2 pl-3.5 pr-2 text-sm text-zinc-700 shadow-xs transition-colors hover:border-brand-300 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:border-brand-700"
    >
      <Plus className="h-4 w-4 text-zinc-400 group-hover:text-brand-600" aria-hidden />
      新建分析
    </Link>
  )
}

/** 折叠后的窄栏：只留图标入口 */
export function SidebarRail({ onExpand }: { onExpand: () => void }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  return (
    <div className="flex h-full flex-col items-center gap-3 py-4">
      <Link to="/" aria-label="log-agent 首页">
        <LogoMark className="h-8 w-8" />
      </Link>
      <NewButton compact />
      <nav aria-label="功能" className="flex flex-col items-center gap-1 pt-2">
        {NAV.map(({ to, icon: Icon, label }) => (
          <Link
            key={to}
            to={to}
            title={label}
            aria-label={label}
            className={`${ICON_BUTTON} ${pathname.startsWith(to) ? 'bg-zinc-200/70 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100' : ''}`}
          >
            <Icon className="h-[18px] w-[18px]" />
          </Link>
        ))}
      </nav>
      <button type="button" onClick={onExpand} aria-label="展开侧边栏" title="展开侧边栏" className={`${ICON_BUTTON} mt-auto`}>
        <PanelLeftOpen className="h-[18px] w-[18px]" />
      </button>
    </div>
  )
}

export function Sidebar({ onNavigate, onCollapse }: { onNavigate?: () => void; onCollapse?: () => void }) {
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
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

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteSession(name),
    onSuccess: (_, name) => {
      setRemoveError(null)
      void client.invalidateQueries({ queryKey: ['sessions'] })
      if (pathname === `/sessions/${encodeURIComponent(name)}` || pathname === `/sessions/${name}`) void navigate({ to: '/' })
    },
    onError: (error, name) => setRemoveError({ name, error }),
  })
  const [removeError, setRemoveError] = useState<{ name: string; error: unknown } | null>(null)

  const groups = useMemo(() => {
    const map = new Map<string, SessionSummary[]>()
    for (const s of sessions.data ?? []) {
      const label = groupLabel(s.updated_at)
      map.set(label, [...(map.get(label) ?? []), s])
    }
    return [...map.entries()]
  }, [sessions.data])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-4 pb-4 pt-4">
        <Brand />
        {onCollapse && (
          <button
            type="button"
            onClick={onCollapse}
            aria-label="收起侧边栏"
            title="收起侧边栏"
            className={`${ICON_BUTTON} ml-auto h-8 w-8`}
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        )}
      </div>

      <div className="space-y-4 px-3">
        <NewButton onNavigate={onNavigate} />
        <nav aria-label="功能" className="space-y-0.5">
          {NAV.map(({ to, icon: Icon, label, hint }) => {
            const active = pathname.startsWith(to)
            return (
              <Link
                key={to}
                to={to}
                onClick={onNavigate}
                title={hint}
                className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                  active
                    ? 'bg-zinc-200/70 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100'
                    : 'text-zinc-600 hover:bg-zinc-200/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100'
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden />
                {label}
              </Link>
            )
          })}
        </nav>
      </div>

      <div className="mt-5 px-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" aria-hidden />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="搜索历史会话"
            aria-label="搜索会话"
            className="w-full rounded-lg bg-zinc-200/50 py-1.5 pl-8 pr-2 text-sm outline-none transition-colors placeholder:text-zinc-400 focus:bg-white focus:ring-2 focus:ring-brand-500/25 dark:bg-zinc-900 dark:focus:bg-zinc-900"
          />
        </div>
      </div>

      <nav aria-label="会话列表" className="mt-3 min-h-0 flex-1 overflow-auto px-3 pb-3">
        {sessions.isLoading && (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        )}
        {removeError && (
          <div role="alert" className="mb-2 space-y-1">
            <ErrorBox error={`删除会话「${removeError.name}」失败：${errorText(removeError.error)}`} />
            <button type="button" onClick={() => setRemoveError(null)} className="px-1 text-xs text-zinc-500 hover:underline">
              知道了
            </button>
          </div>
        )}
        {sessions.error && (
          <div role="alert" className="mb-2 space-y-1">
            <ErrorBox error={`${sessions.data ? '刷新会话列表失败，下面是上次加载的结果' : '加载会话列表失败'}：${errorText(sessions.error)}`} />
            <button
              type="button"
              onClick={() => void sessions.refetch()}
              disabled={sessions.isFetching}
              className="px-1 text-xs text-brand-700 hover:underline disabled:opacity-60 dark:text-brand-400"
            >
              {sessions.isFetching ? '重试中…' : '重试'}
            </button>
          </div>
        )}
        {sessions.data?.length === 0 && (
          <p className="px-2 py-6 text-center text-xs leading-relaxed text-zinc-500">
            {q ? `没有匹配「${q}」的会话` : '还没有会话，点上方「新建分析」开始'}
          </p>
        )}
        {groups.map(([label, items]) => (
          <div key={label} className="mb-4">
            <h3 className="px-2.5 pb-1.5 text-xs font-medium text-zinc-400">{label}</h3>
            <ul className="space-y-px">
              {items.map((s) => {
                const active = pathname === `/sessions/${encodeURIComponent(s.name)}` || pathname === `/sessions/${s.name}`
                const tone = s.last?.assessment ? ASSESSMENT[s.last.assessment].tone : 'gray'
                return (
                  <li key={s.name} className="group relative">
                    <Link
                      to="/sessions/$name"
                      params={{ name: s.name }}
                      onClick={onNavigate}
                      title={s.title || s.name}
                      aria-current={active ? 'page' : undefined}
                      className={`block rounded-lg px-2.5 py-2 pr-8 transition-colors ${
                        active ? 'bg-zinc-200/70 dark:bg-zinc-800' : 'hover:bg-zinc-200/50 dark:hover:bg-zinc-800/60'
                      }`}
                    >
                      <span
                        className={`block truncate text-sm ${active ? 'font-medium text-zinc-900 dark:text-zinc-50' : 'text-zinc-700 dark:text-zinc-200'}`}
                      >
                        {s.title || '新的分析'}
                      </span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-xs text-zinc-400">
                        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden />
                        <span className="truncate">
                          {s.logs[0]?.name ?? '无日志'}
                          {s.logs.length > 1 ? ` +${s.logs.length - 1}` : ''} · {s.turns} 轮
                          {s.origin === 'analyze' ? ' · CLI' : ''}
                        </span>
                      </span>
                    </Link>
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(`删除会话「${s.title || s.name}」？对话记录与报告会一并删除，日志文件不受影响。`)) {
                          setRemoveError(null)
                          remove.mutate(s.name)
                        }
                      }}
                      aria-label={`删除会话 ${s.title || s.name}`}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-zinc-400 opacity-0 hover:bg-zinc-300/60 hover:text-red-600 focus:opacity-100 group-hover:opacity-100 dark:hover:bg-zinc-700"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>

      {meta.data && (
        <div className="flex items-center gap-2 px-4 py-3 text-xs text-zinc-500" title={meta.data.db ?? undefined}>
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${meta.data.can_chat ? 'bg-emerald-500' : 'bg-amber-500'}`} aria-hidden />
          <span className="min-w-0 flex-1 truncate">{meta.data.can_chat ? '模型已连接' : '只读模式'}</span>
          <span className="shrink-0 font-mono text-zinc-400">v{meta.data.version}</span>
        </div>
      )}
    </div>
  )
}

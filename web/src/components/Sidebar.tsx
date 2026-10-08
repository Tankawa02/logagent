import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import { Activity, GanttChart, MessageSquare, Plus, Search, Trash2, Zap } from 'lucide-react'
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
  blue: 'bg-sky-500',
}

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
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
      <div className="flex items-center gap-2 px-4 pb-3 pt-4">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900">
          <Activity className="h-4 w-4" aria-hidden />
        </span>
        <span className="font-semibold tracking-tight">log-agent</span>
        {meta.data && <span className="ml-auto text-xs text-zinc-400">v{meta.data.version}</span>}
      </div>

      <div className="space-y-2 px-3">
        <Link
          to="/"
          onClick={onNavigate}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
        >
          <Plus className="h-4 w-4" aria-hidden />
          新建分析
        </Link>
        <Link
          to="/trace"
          onClick={onNavigate}
          className={`flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-sm ${
            pathname.startsWith('/trace')
              ? 'bg-zinc-200/70 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100'
              : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800/60'
          }`}
        >
          <GanttChart className="h-4 w-4" aria-hidden />
          Trace
          <span className="ml-auto text-xs text-zinc-400">模型 · 耗时 · 工具</span>
        </Link>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" aria-hidden />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="搜索会话、问题或日志"
            aria-label="搜索会话"
            className="w-full rounded-lg border border-zinc-200 bg-white py-1.5 pl-8 pr-2 text-sm outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 dark:border-zinc-800 dark:bg-zinc-950"
          />
        </div>
      </div>

      <nav aria-label="会话列表" className="mt-3 min-h-0 flex-1 overflow-auto px-2 pb-3">
        {sessions.isLoading && (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        )}
        {removeError && (
          <div role="alert" className="mb-2 space-y-1 px-1">
            <ErrorBox error={`删除会话「${removeError.name}」失败：${errorText(removeError.error)}`} />
            <button type="button" onClick={() => setRemoveError(null)} className="px-1 text-xs text-zinc-500 hover:underline">
              知道了
            </button>
          </div>
        )}
        {sessions.error && (
          <div role="alert" className="mb-2 space-y-1 px-1">
            <ErrorBox error={`${sessions.data ? '刷新会话列表失败，下面是上次加载的结果' : '加载会话列表失败'}：${errorText(sessions.error)}`} />
            <button
              type="button"
              onClick={() => void sessions.refetch()}
              disabled={sessions.isFetching}
              className="px-1 text-xs text-sky-700 hover:underline disabled:opacity-60 dark:text-sky-400"
            >
              {sessions.isFetching ? '重试中…' : '重试'}
            </button>
          </div>
        )}
        {sessions.data?.length === 0 && (
          <p className="px-3 py-6 text-center text-xs text-zinc-500">{q ? `没有匹配「${q}」的会话` : '还没有会话，点上方「新建分析」开始'}</p>
        )}
        {groups.map(([label, items]) => (
          <div key={label} className="mb-3">
            <h3 className="px-2 pb-1 text-xs font-medium text-zinc-400">{label}</h3>
            <ul className="space-y-0.5">
              {items.map((s) => {
                const active = pathname === `/sessions/${encodeURIComponent(s.name)}` || pathname === `/sessions/${s.name}`
                const tone = s.last?.assessment ? ASSESSMENT[s.last.assessment].tone : 'gray'
                const Icon = s.origin === 'analyze' ? Zap : MessageSquare
                return (
                  <li key={s.name} className="group relative">
                    <Link
                      to="/sessions/$name"
                      params={{ name: s.name }}
                      onClick={onNavigate}
                      title={s.title || s.name}
                      className={`flex items-start gap-2 rounded-lg px-2 py-2 pr-8 text-sm ${
                        active ? 'bg-zinc-200/70 dark:bg-zinc-800' : 'hover:bg-zinc-100 dark:hover:bg-zinc-800/60'
                      }`}
                    >
                      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-400" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-zinc-800 dark:text-zinc-100">{s.title || '新的分析'}</span>
                        <span className="flex items-center gap-1.5 text-xs text-zinc-400">
                          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden />
                          <span className="truncate">
                            {s.logs[0]?.name ?? '无日志'}
                            {s.logs.length > 1 ? ` +${s.logs.length - 1}` : ''} · {s.turns} 轮
                          </span>
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
                      className="absolute right-1.5 top-2 rounded p-1 text-zinc-400 opacity-0 hover:bg-zinc-200 hover:text-red-600 focus:opacity-100 group-hover:opacity-100 dark:hover:bg-zinc-700"
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
        <div className="space-y-1 border-t border-zinc-200 px-4 py-3 text-xs text-zinc-500 dark:border-zinc-800">
          <div className="flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${meta.data.can_chat ? 'bg-emerald-500' : 'bg-amber-500'}`} aria-hidden />
            {meta.data.can_chat ? '模型已连接，可在网页提问' : '只读：未配置 API Key 或 --read-only'}
          </div>
          {meta.data.db && (
            <p className="truncate font-mono text-zinc-400" title={meta.data.db}>
              {meta.data.db}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

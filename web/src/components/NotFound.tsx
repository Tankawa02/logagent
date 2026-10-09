import { Link, useRouterState } from '@tanstack/react-router'
import { ArrowLeft, Compass, Plus } from 'lucide-react'

const SHORTCUTS = [
  { to: '/trace', label: '执行记录' },
  { to: '/skills', label: '技能' },
  { to: '/memory', label: '记忆' },
  { to: '/settings', label: '设置' },
] as const

export function NotFound() {
  const pathname = useRouterState({ select: (s) => s.location.pathname })

  return (
    <main className="flex min-h-dvh items-center justify-center bg-zinc-50 px-4 dark:bg-zinc-950">
      <div className="w-full max-w-md text-center">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-100 dark:bg-brand-950/40 dark:text-brand-300 dark:ring-brand-900">
          <Compass className="h-6 w-6" aria-hidden />
        </span>
        <p className="mt-5 font-mono text-xs text-zinc-400 dark:text-zinc-500">404</p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">页面不存在</h1>
        <p className="mt-2 text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
          没有找到{' '}
          <code className="break-all rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
            {pathname}
          </code>
          ，链接可能写错了，或者对应的会话已被删除。
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400"
          >
            <Plus className="h-4 w-4" aria-hidden />
            新建分析
          </Link>
          <button
            type="button"
            onClick={() => history.back()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 shadow-xs transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
            返回上一页
          </button>
        </div>
        <nav aria-label="常用页面" className="mt-8 flex flex-wrap justify-center gap-x-4 gap-y-1 text-sm">
          {SHORTCUTS.map((s) => (
            <Link
              key={s.to}
              to={s.to}
              className="text-zinc-500 hover:text-brand-700 hover:underline dark:text-zinc-400 dark:hover:text-brand-300"
            >
              {s.label}
            </Link>
          ))}
        </nav>
      </div>
    </main>
  )
}

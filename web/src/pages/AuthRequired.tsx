import { KeyRound } from 'lucide-react'

export function AuthRequired() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-zinc-50 p-6 dark:bg-zinc-950">
      <div className="w-full max-w-md space-y-3 rounded-xl border border-zinc-200 bg-white p-6 shadow-xs dark:border-zinc-800 dark:bg-zinc-900">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-zinc-100 dark:bg-zinc-800">
          <KeyRound className="h-4 w-4" aria-hidden />
        </span>
        <h1 className="text-lg font-semibold">需要访问令牌</h1>
        <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
          请使用 <code className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">log-agent serve</code> 启动时终端里打印的完整链接（带{' '}
          <code className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">?token=</code>
          ）打开。同事交接请让会话所有者在会话页点「分享」生成只读链接。
        </p>
      </div>
    </div>
  )
}

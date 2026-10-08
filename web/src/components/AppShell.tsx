import { Menu, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Sidebar } from './Sidebar'

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex h-dvh overflow-hidden bg-white dark:bg-zinc-950">
      <aside className="hidden w-72 shrink-0 border-r border-zinc-200 bg-zinc-50 md:block dark:border-zinc-800 dark:bg-zinc-900/60">
        <Sidebar />
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button type="button" aria-label="关闭侧边栏" className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="relative h-full w-[85%] max-w-xs border-r border-zinc-200 bg-zinc-50 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="关闭侧边栏"
              className="absolute right-2 top-3.5 rounded p-1 text-zinc-500 hover:bg-zinc-200 dark:hover:bg-zinc-800"
            >
              <X className="h-4 w-4" />
            </button>
            <Sidebar onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-zinc-200 px-3 py-2 md:hidden dark:border-zinc-800">
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="打开侧边栏"
            className="rounded p-1.5 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <Menu className="h-5 w-5" />
          </button>
          <span className="text-sm font-semibold">log-agent</span>
        </div>
        <main className="min-h-0 flex-1">{children}</main>
      </div>
    </div>
  )
}

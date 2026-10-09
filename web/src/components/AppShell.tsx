import { Menu, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Brand, Sidebar, SidebarRail } from './Sidebar'

const COLLAPSE_KEY = 'log-agent:sidebar-collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(readCollapsed)

  function toggleCollapsed(next: boolean) {
    setCollapsed(next)
    try {
      localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0')
    } catch {
      // 隐私模式下写不了本地存储，只是不记住折叠状态
    }
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      <aside className={`hidden shrink-0 transition-[width] duration-200 md:block ${collapsed ? 'w-16' : 'w-64'}`} aria-label="侧边栏">
        {collapsed ? <SidebarRail onExpand={() => toggleCollapsed(false)} /> : <Sidebar onCollapse={() => toggleCollapsed(true)} />}
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            aria-label="关闭侧边栏"
            className="absolute inset-0 bg-zinc-950/30 backdrop-blur-[2px]"
            onClick={() => setOpen(false)}
          />
          <aside className="relative h-full w-[85%] max-w-xs bg-zinc-50 shadow-2xl dark:bg-zinc-950">
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="关闭侧边栏"
              className="absolute right-3 top-4 z-10 rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-200/70 dark:hover:bg-zinc-800"
            >
              <X className="h-4 w-4" />
            </button>
            <Sidebar onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      {/* 主区是一张浮在侧边栏底色上的纸面，桌面端带一圈留白和圆角 */}
      <div className="flex min-w-0 flex-1 flex-col md:py-2 md:pr-2">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-paper md:rounded-2xl md:border md:border-zinc-200/70 md:shadow-xs dark:bg-paper-dark md:dark:border-zinc-800">
          <div className="flex items-center gap-2 border-b border-zinc-200/70 px-3 py-2 md:hidden dark:border-zinc-800">
            <button
              type="button"
              onClick={() => setOpen(true)}
              aria-label="打开侧边栏"
              className="rounded-lg p-1.5 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              <Menu className="h-5 w-5" />
            </button>
            <Brand />
          </div>
          <main className="min-h-0 flex-1">{children}</main>
        </div>
      </div>
    </div>
  )
}

import { useNavigate } from '@tanstack/react-router'
import { Menu, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useMediaQuery } from '../lib/hooks'
import { hasMod, isEditable, PRIMARY_INPUT_ATTR } from '../lib/shortcuts'
import { ConnectionBanner } from './ConnectionBanner'
import { onOpenShortcuts, ShortcutsDialog } from './ShortcutsDialog'
import { Brand, Sidebar, SidebarRail } from './Sidebar'

const COLLAPSE_KEY = 'log-agent:sidebar-collapsed'

/** 只有用户手动折叠/展开过才有值；没设置过时按屏宽决定 */
function readCollapsed(): boolean | null {
  try {
    const value = localStorage.getItem(COLLAPSE_KEY)
    return value === '1' ? true : value === '0' ? false : null
  } catch {
    return null
  }
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [stored, setStored] = useState(readCollapsed)
  // 768–1023px 时 256px 的侧边栏会把主区挤得很窄，默认收成图标栏
  const narrow = !useMediaQuery('(min-width: 1024px)')
  const collapsed = stored ?? narrow
  const [searchNonce, setSearchNonce] = useState(0)
  const [shortcuts, setShortcuts] = useState(false)
  useEffect(() => onOpenShortcuts(() => setShortcuts(true)), [])
  const navigate = useNavigate()
  const desktop = useMediaQuery('(min-width: 768px)')
  const latest = useRef({ collapsed, desktop })
  latest.current = { collapsed, desktop }

  // 全局快捷键：⌘K 搜索会话、⌘⇧O 新建分析、/ 聚焦主输入框
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing) return
      const key = event.key.toLowerCase()
      if (hasMod(event) && !event.shiftKey && !event.altKey && key === 'k') {
        event.preventDefault()
        const { collapsed: isCollapsed, desktop: isDesktop } = latest.current
        if (isDesktop && isCollapsed) toggleCollapsed(false)
        if (!isDesktop) setOpen(true)
        setSearchNonce((n) => n + 1)
      } else if (hasMod(event) && event.shiftKey && !event.altKey && key === 'o') {
        event.preventDefault()
        setOpen(false)
        void navigate({ to: '/' })
      } else if (event.key === '?' && !event.metaKey && !event.ctrlKey && !event.altKey && !isEditable(event.target)) {
        if (document.querySelector('[aria-modal="true"]')) return
        event.preventDefault()
        setShortcuts(true)
      } else if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey && !isEditable(event.target)) {
        if (document.querySelector('[aria-modal="true"]')) return
        const input = document.querySelector<HTMLElement>(`[${PRIMARY_INPUT_ATTR}]`)
        if (!input) return
        event.preventDefault()
        input.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [navigate])

  function toggleCollapsed(next: boolean) {
    setStored(next)
    try {
      localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0')
    } catch {
      // 隐私模式下写不了本地存储，只是不记住折叠状态
    }
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      <aside className={`hidden shrink-0 transition-[width] duration-200 md:block ${collapsed ? 'w-16' : 'w-64'}`} aria-label="侧边栏">
        {collapsed ? (
          <SidebarRail onExpand={() => toggleCollapsed(false)} />
        ) : (
          <Sidebar onCollapse={() => toggleCollapsed(true)} focusSearch={searchNonce} />
        )}
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
            <Sidebar onNavigate={() => setOpen(false)} focusSearch={searchNonce} />
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
          <ConnectionBanner />
          <main className="min-h-0 flex-1">{children}</main>
        </div>
      </div>
      {shortcuts && <ShortcutsDialog onClose={() => setShortcuts(false)} />}
    </div>
  )
}

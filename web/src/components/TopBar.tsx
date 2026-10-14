import { Menu } from 'lucide-react'
import { createContext, useContext, type ReactNode } from 'react'

interface ShellValue {
  /** 小屏上打开侧边栏抽屉 */
  openNav: () => void
}

export const ShellContext = createContext<ShellValue | null>(null)

/** 分享页没有应用外壳，返回 null，顶部栏就不显示菜单按钮 */
export function useShell() {
  return useContext(ShellContext)
}

export function MobileNavButton() {
  const shell = useShell()
  if (!shell) return null
  return (
    <button
      type="button"
      onClick={shell.openNav}
      aria-label="打开导航"
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-foreground/80 transition-colors hover:bg-accent md:hidden"
    >
      <Menu className="size-4" aria-hidden />
    </button>
  )
}

/** 每个页面顶部的 h-14 标题栏：左侧标题 / 面包屑，右侧操作 */
export function TopBar({ children, actions, className = '' }: { children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <header className={`flex h-14 shrink-0 items-center gap-2 border-b border-border px-3 sm:px-5 ${className}`}>
      <MobileNavButton />
      <div className="flex min-w-0 flex-1 items-center gap-2">{children}</div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}

export function TopBarTitle({ children }: { children: ReactNode }) {
  return <h1 className="truncate font-heading text-[15px] font-semibold tracking-tight text-foreground">{children}</h1>
}

const WIDTHS = { md: 'max-w-3xl', lg: 'max-w-5xl', xl: 'max-w-7xl' } as const

/** 管理类页面的统一骨架：顶部栏 + 可滚动内容区（居中、限宽） */
export function PageScaffold({
  title,
  description,
  actions,
  width = 'lg',
  children,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  width?: keyof typeof WIDTHS
  children: ReactNode
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar actions={actions}>
        <TopBarTitle>{title}</TopBarTitle>
      </TopBar>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className={`mx-auto space-y-6 px-4 py-6 sm:px-6 ${WIDTHS[width]}`}>
          {description && <p className="max-w-2xl text-pretty text-sm leading-6 text-muted-foreground">{description}</p>}
          {children}
        </div>
      </div>
    </div>
  )
}

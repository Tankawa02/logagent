import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { twMerge } from 'tailwind-merge'
import { TONE_CLASS, type Tone } from '../lib/format'

export function Badge({ tone = 'gray', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-border bg-card text-card-foreground ${className}`}>{children}</section>
}

/** fill：标题占满剩余宽度、可截断，操作按钮始终和标题同一行（标题是长路径等可变内容时用） */
export function CardHeader({ title, children, fill = false }: { title: ReactNode; children?: ReactNode; fill?: boolean }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
      <h2 className={`min-w-0 max-w-full text-sm font-semibold text-foreground ${fill ? 'flex-1 basis-0' : ''}`}>{title}</h2>
      {children && <div className={`flex flex-wrap items-center gap-2 ${fill ? 'shrink-0' : ''}`}>{children}</div>}
    </div>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'destructive'
  size?: 'sm' | 'xs' | 'md' | 'icon' | 'icon-sm' | 'icon-xs'
}

const BUTTON_VARIANT = {
  primary: 'bg-primary text-primary-foreground shadow-xs hover:bg-primary/90',
  secondary: 'border border-border bg-background text-foreground shadow-xs hover:bg-accent',
  ghost: 'text-foreground/80 hover:bg-accent hover:text-foreground',
  danger:
    'border border-red-200 bg-background text-red-700 hover:bg-red-50 dark:border-red-500/30 dark:text-red-300 dark:hover:bg-red-500/10',
  destructive: 'bg-red-600 text-white shadow-xs hover:bg-red-700 dark:hover:bg-red-500',
} as const

const BUTTON_SIZE = {
  md: 'h-9 px-4',
  sm: 'h-8 px-3',
  xs: 'h-6 gap-1 px-2 text-xs',
  icon: 'size-9',
  'icon-sm': 'size-8',
  'icon-xs': 'size-6',
} as const

/** 调用方传入的 className 会覆盖默认的圆角、内边距等（用 tailwind-merge 去掉冲突的类） */
export function buttonClass(variant: keyof typeof BUTTON_VARIANT = 'secondary', size: keyof typeof BUTTON_SIZE = 'sm', className = '') {
  return twMerge(
    'inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:pointer-events-none disabled:opacity-50',
    BUTTON_VARIANT[variant],
    BUTTON_SIZE[size],
    className,
  )
}

export function Button({ variant = 'secondary', size = 'sm', className = '', ...props }: ButtonProps) {
  return <button type="button" className={buttonClass(variant, size, className)} {...props} />
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`motion-essential inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-300 border-t-zinc-700 dark:border-zinc-700 dark:border-t-zinc-200 ${className}`}
    />
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <span aria-hidden className={`block animate-pulse rounded-md bg-accent motion-reduce:animate-none ${className}`} />
}

/** 页面级加载占位：形状接近真实内容，加载完成时不会整块跳动 */
export function PageSkeleton({ label = '加载中' }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="mx-auto w-full max-w-3xl space-y-8 px-4 pt-10 sm:px-6">
      <span className="sr-only">{label}…</span>
      <div className="space-y-3">
        <Skeleton className="h-7 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
      </div>
      <div className="space-y-2.5">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-11/12" />
        <Skeleton className="h-4 w-4/5" />
      </div>
      <div className="space-y-2.5">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </div>
    </div>
  )
}

const ROW_WIDTHS = ['w-3/4', 'w-2/3', 'w-5/6', 'w-1/2', 'w-4/5', 'w-3/5']

/** 卡片 + 行列表占位（记忆、skill、对话列表等） */
export function ListSkeleton({ rows = 4, label = '加载中', className = '' }: { rows?: number; label?: string; className?: string }) {
  return (
    <div
      role="status"
      aria-label={label}
      className={`rounded-xl border border-zinc-200/80 bg-white dark:border-zinc-800 dark:bg-zinc-900 ${className}`}
    >
      <span className="sr-only">{label}…</span>
      <div className="border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
        <Skeleton className="h-4 w-28" />
      </div>
      <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="space-y-2 px-4 py-3.5">
            <Skeleton className={`h-3.5 ${ROW_WIDTHS[i % ROW_WIDTHS.length]}`} />
            <Skeleton className="h-3 w-24" />
          </div>
        ))}
      </div>
    </div>
  )
}

/** 表单卡片占位（设置页） */
export function FormSkeleton({ fields = 4, label = '加载中' }: { fields?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="rounded-xl border border-zinc-200/80 bg-white dark:border-zinc-800 dark:bg-zinc-900">
      <span className="sr-only">{label}…</span>
      <div className="border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
        <Skeleton className="h-4 w-24" />
      </div>
      <div className="grid gap-5 px-4 py-5 sm:grid-cols-2">
        {Array.from({ length: fields }, (_, i) => (
          <div key={i} className="space-y-2">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-8 w-full" />
          </div>
        ))}
      </div>
    </div>
  )
}

/** 统计卡片 + 列表占位（Trace 概览） */
export function StatsSkeleton({ label = '加载中' }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-5">
      <span className="sr-only">{label}…</span>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="space-y-2 rounded-xl border border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-6 w-20" />
          </div>
        ))}
      </div>
      <ListSkeleton rows={5} label={label} />
    </div>
  )
}

/** 应用外壳占位：首次读取服务信息时，先画出侧边栏和内容区轮廓 */
export function ShellSkeleton() {
  return (
    <div className="flex h-dvh bg-sidebar">
      <div aria-hidden className="hidden w-64 shrink-0 space-y-6 px-3 py-4 md:block">
        <Skeleton className="h-6 w-28" />
        <Skeleton className="h-9 w-full rounded-full" />
        <div className="space-y-2.5">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className={`h-4 ${ROW_WIDTHS[i]}`} />
          ))}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col md:py-2 md:pr-2">
        <div className="min-h-0 flex-1 bg-background md:rounded-2xl md:border md:border-border md:shadow-sm">
          <PageSkeleton label="正在连接服务" />
        </div>
      </div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-10 text-center text-sm text-zinc-500 dark:text-zinc-400">{children}</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
      {message}
    </div>
  )
}

export function List({ items, empty = '待确认 / 暂无信息' }: { items: string[]; empty?: string }) {
  if (!items.length) return <p className="text-sm text-zinc-400 dark:text-zinc-500">{empty}</p>
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  )
}

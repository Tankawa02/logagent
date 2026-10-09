import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { TONE_CLASS, type Tone } from '../lib/format'

export function Badge({ tone = 'gray', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-xl border border-zinc-200/80 bg-white dark:border-zinc-800 dark:bg-zinc-900 ${className}`}
    >
      {children}
    </section>
  )
}

  /** fill：标题占满剩余宽度、可截断，操作按钮始终和标题同一行（标题是长路径等可变内容时用） */
  export function CardHeader({ title, children, fill = false }: { title: ReactNode; children?: ReactNode; fill?: boolean }) {
  return (
  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
  <h2 className={`min-w-0 max-w-full text-sm font-semibold text-zinc-800 dark:text-zinc-100 ${fill ? 'flex-1 basis-0' : ''}`}>{title}</h2>
  {children && <div className={`flex flex-wrap items-center gap-2 ${fill ? 'shrink-0' : ''}`}>{children}</div>}
    </div>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }

export function Button({ variant = 'secondary', className = '', ...props }: ButtonProps) {
  const styles = {
    primary: 'bg-brand-600 text-white hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400 disabled:opacity-40',
    secondary:
      'border border-zinc-200 bg-white text-zinc-700 shadow-xs hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-40',
    ghost: 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800 disabled:opacity-40',
    danger: 'border border-red-200 text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950',
  }[variant]
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed ${styles} ${className}`}
      {...props}
    />
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-300 border-t-zinc-700 dark:border-zinc-700 dark:border-t-zinc-200 ${className}`}
    />
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

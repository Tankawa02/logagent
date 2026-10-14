import { CircleCheck, CirclePause, CircleX } from 'lucide-react'
import { TURN_STATUS } from '../lib/format'

export const STATUS_STYLE: Record<string, { icon: typeof CircleCheck; className: string; bar: string }> = {
  ok: { icon: CircleCheck, className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300', bar: 'bg-primary' },
  error: { icon: CircleX, className: 'bg-red-50 text-red-700 dark:bg-red-500/15 dark:text-red-300', bar: 'bg-red-500' },
  interrupted: { icon: CirclePause, className: 'bg-amber-50 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300', bar: 'bg-amber-500' },
}

export function TraceStatusBadge({ status }: { status: string }) {
  const meta = TURN_STATUS[status] ?? TURN_STATUS.ok
  const { icon: Icon, className } = STATUS_STYLE[status] ?? STATUS_STYLE.ok
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${className}`}>
      <Icon className="size-3.5" aria-hidden />
      {meta.label}
    </span>
  )
}

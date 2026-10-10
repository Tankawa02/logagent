import { Check } from 'lucide-react'
import type { ReactNode } from 'react'
import type { BoundCheck } from '../lib/time-input'

export const FIELD =
  'w-full rounded-lg border bg-white px-3 py-2 text-sm outline-none transition-colors placeholder:text-zinc-400 focus:ring-4 dark:bg-zinc-950'
export const FIELD_OK = 'border-zinc-200 focus:border-brand-400 focus:ring-brand-500/10 dark:border-zinc-700'
export const FIELD_BAD = 'border-red-300 focus:border-red-400 focus:ring-red-500/10 dark:border-red-800'

export function TimeField({
  id,
  label,
  value,
  onChange,
  placeholder,
  check,
  hint,
  wide = false,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder: string
  check: BoundCheck | null
  hint?: ReactNode
  wide?: boolean
}) {
  const bad = !!check && !check.ok
  const noteId = `${id}-note`
  return (
    <div className={`space-y-1.5 ${wide ? 'sm:col-span-2' : ''}`}>
      <label htmlFor={id} className="block text-xs font-medium text-zinc-600 dark:text-zinc-300">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        aria-invalid={bad || undefined}
        aria-describedby={check || hint ? noteId : undefined}
        className={`${FIELD} ${bad ? FIELD_BAD : FIELD_OK}`}
      />
      {check ? (
        <p
          id={noteId}
          className={`flex items-start gap-1 text-xs ${bad ? 'text-red-700 dark:text-red-300' : 'text-emerald-700 dark:text-emerald-400'}`}
        >
          {!bad && <Check className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />}
          {check.ok ? check.text : check.error}
        </p>
      ) : (
        hint && (
          <p id={noteId} className="text-xs text-zinc-500 dark:text-zinc-400">
            {hint}
          </p>
        )
      )}
    </div>
  )
}

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** 把某一时刻按指定时区格式化成 time-input 认得的 `YYYY-MM-DD HH:MM`；时区名不认识时退回 UTC */
function formatIn(date: Date, timezone: string): { day: string; minute: string } {
  // 会话时区也可能写成 +08:00 这样的固定偏移：换算到 UTC 再格式化
  const offset = /^(?:UTC|GMT)?([+-])(\d{1,2}):?(\d{2})?$/i.exec(timezone.trim())
  if (offset) {
    const minutes = (Number(offset[2]) * 60 + Number(offset[3] ?? 0)) * (offset[1] === '-' ? -1 : 1)
    return formatIn(new Date(date.getTime() + minutes * 60_000), 'UTC')
  }
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(date)
  } catch {
    return formatIn(date, 'UTC')
  }
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '00'
  const day = `${get('year')}-${get('month')}-${get('day')}`
  return { day, minute: `${day} ${get('hour')}:${get('minute')}` }
}

const ago = (ms: number, tz: string) => formatIn(new Date(Date.now() - ms), tz).minute

export const TIME_PRESETS: { label: string; range: (tz: string) => [string, string] }[] = [
  { label: '最近 15 分钟', range: (tz) => [ago(15 * 60_000, tz), ''] },
  { label: '最近 1 小时', range: (tz) => [ago(3600_000, tz), ''] },
  { label: '最近 24 小时', range: (tz) => [ago(86400_000, tz), ''] },
  { label: '今天', range: (tz) => [`${formatIn(new Date(), tz).day} 00:00`, ''] },
  {
    label: '昨天',
    range: (tz) => {
      const day = formatIn(new Date(Date.now() - 86400_000), tz).day
      return [`${day} 00:00`, `${day} 23:59`]
    },
  },
]

/** 时间窗口的快捷选项：按日志所用的时区填入开始 / 结束，用户可以再改 */
export function TimePresets({
  timezone,
  onPick,
  onClear,
  active,
}: {
  timezone: string
  onPick: (since: string, until: string) => void
  onClear?: () => void
  active: boolean
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 sm:col-span-2" role="group" aria-label="时间范围快捷选项">
      <span className="text-xs text-zinc-500 dark:text-zinc-400">快捷：</span>
      {TIME_PRESETS.map((preset) => (
        <button
          key={preset.label}
          type="button"
          onClick={() => {
            const [since, until] = preset.range(timezone)
            onPick(since, until)
          }}
          className="rounded-full border border-zinc-200 bg-white px-2.5 py-0.5 text-xs text-zinc-600 transition-colors hover:border-brand-300 hover:text-brand-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:text-brand-300"
        >
          {preset.label}
        </button>
      ))}
      {active && onClear && (
        <button
          type="button"
          onClick={onClear}
          className="rounded-full px-2 py-0.5 text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          清除范围
        </button>
      )}
    </div>
  )
}

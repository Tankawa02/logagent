import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { setThemePreference, useThemePreference, type ThemePreference } from '../lib/theme'

const OPTIONS: { value: ThemePreference; label: string; icon: LucideIcon }[] = [
  { value: 'system', label: '跟随系统', icon: Monitor },
  { value: 'light', label: '亮色', icon: Sun },
  { value: 'dark', label: '暗色', icon: Moon },
]

/** 展开侧边栏里用的分段切换 */
export function ThemeToggle() {
  const current = useThemePreference()
  return (
    <div role="radiogroup" aria-label="主题" className="flex items-center gap-0.5 rounded-lg bg-zinc-100 p-0.5 dark:bg-zinc-800/80">
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = current === value
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => setThemePreference(value)}
            className={`flex h-7 flex-1 items-center justify-center rounded-md transition-colors focus-visible:outline-2 focus-visible:outline-brand-500 ${
              active
                ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-zinc-50'
                : 'text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200'
            }`}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
          </button>
        )
      })}
    </div>
  )
}

/** 收起的侧边栏里用：单个按钮依次切换 系统 → 亮色 → 暗色 */
export function ThemeCycleButton({ className = '' }: { className?: string }) {
  const current = useThemePreference()
  const index = OPTIONS.findIndex((o) => o.value === current)
  const { icon: Icon, label } = OPTIONS[index]
  const next = OPTIONS[(index + 1) % OPTIONS.length]
  return (
    <button
      type="button"
      onClick={() => setThemePreference(next.value)}
      aria-label={`主题：${label}，点击切换为${next.label}`}
      title={`主题：${label}`}
      className={className}
    >
      <Icon className="h-[18px] w-[18px]" aria-hidden />
    </button>
  )
}

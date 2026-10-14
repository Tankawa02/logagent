import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { setThemePreference, toggleResolvedTheme, useResolvedDark, useThemePreference, type ThemePreference } from '../lib/theme'

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

/** 侧边栏底部的单按钮：图标表示当前外观，点击在亮 / 暗之间切换 */
export function ThemeCycleButton({ className = '' }: { className?: string }) {
  const preference = useThemePreference()
  const dark = useResolvedDark()
  const Icon = dark ? Moon : Sun
  const label = `${dark ? '暗色' : '亮色'}${preference === 'system' ? '（跟随系统）' : ''}`
  const nextLabel = dark ? '亮色' : '暗色'
  return (
    <button
      type="button"
      onClick={toggleResolvedTheme}
      aria-label={`主题：${label}，点击切换为${nextLabel}`}
      title={`主题：${label}，点击切换为${nextLabel}`}
      className={className}
    >
      <Icon className="h-[18px] w-[18px]" aria-hidden />
    </button>
  )
}

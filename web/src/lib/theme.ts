import { useSyncExternalStore } from 'react'

export type ThemePreference = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'log-agent-theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()

function readPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

let preference = readPreference()
let transitionTimer = 0

function resolveDark() {
  return preference === 'dark' || (preference === 'system' && media.matches)
}

function apply() {
  const dark = resolveDark()
  const root = document.documentElement
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
    meta.setAttribute('content', dark ? '#191a1a' : '#fcfcf9')
  })
}

media.addEventListener('change', () => {
  if (preference !== 'system') return
  apply()
  listeners.forEach((listener) => listener())
})

window.addEventListener('storage', (event) => {
  if (event.key !== STORAGE_KEY) return
  preference = readPreference()
  apply()
  listeners.forEach((listener) => listener())
})

export function setThemePreference(next: ThemePreference) {
  preference = next
  try {
    if (next === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // 隐私模式下写不进去也不影响本次切换
  }
  const root = document.documentElement
  root.classList.add('theme-transition')
  window.clearTimeout(transitionTimer)
  transitionTimer = window.setTimeout(() => root.classList.remove('theme-transition'), 200)
  apply()
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, () => preference)
}

/** 实际生效的主题（「跟随系统」已按系统设置换算） */
export function useResolvedDark(): boolean {
  return useSyncExternalStore(subscribe, resolveDark)
}

/**
 * 在亮 / 暗之间切换实际外观，每次点击都一定有可见变化。
 * 切到与系统一致的外观时回到「跟随系统」，不必再手动改回来。
 */
export function toggleResolvedTheme() {
  const nextDark = !resolveDark()
  setThemePreference(nextDark === media.matches ? 'system' : nextDark ? 'dark' : 'light')
}

apply()

import { useSyncExternalStore } from 'react'

// 删除会话先只在界面上隐藏，几秒后才真正调用接口，期间可以撤销。
// 状态放在模块里而不是侧边栏组件里：折叠 / 收起侧边栏不会把计时器一起卸掉。

export const UNDO_MS = 5000

export interface DeferredDelete {
  name: string
  label: string
}

interface State {
  pending: DeferredDelete | null
  failed: { item: DeferredDelete; error: unknown } | null
}

let state: State = { pending: null, failed: null }
let timer: ReturnType<typeof setTimeout> | undefined
let commit: (() => Promise<unknown>) | null = null
const listeners = new Set<() => void>()

function set(next: Partial<State>) {
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}

function run(item: DeferredDelete, action: () => Promise<unknown>) {
  action().catch((error: unknown) => set({ failed: { item, error } }))
}

/** 立即执行还在等待的删除（新的删除进来、或页面要关闭时） */
export function flushDelete() {
  clearTimeout(timer)
  const item = state.pending
  const action = commit
  commit = null
  if (item && action) {
    set({ pending: null })
    run(item, action)
  }
}

export function scheduleDelete(item: DeferredDelete, action: () => Promise<unknown>, delay = UNDO_MS) {
  flushDelete()
  commit = action
  set({ pending: item, failed: null })
  timer = setTimeout(flushDelete, delay)
}

export function undoDelete() {
  clearTimeout(timer)
  commit = null
  set({ pending: null })
}

export function dismissDeleteError() {
  set({ failed: null })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useDeferredDelete(): State {
  return useSyncExternalStore(subscribe, () => state)
}

if (typeof window !== 'undefined') {
  // 关页面时不丢掉用户已经确认的删除：用 keepalive 请求在卸载期间发出去
  window.addEventListener('pagehide', flushDelete)
}

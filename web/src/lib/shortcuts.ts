const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)

/** 快捷键提示里的修饰键：Mac 显示 ⌘，其他系统显示 Ctrl */
export const MOD = isMac ? '⌘' : 'Ctrl'

export function hasMod(event: KeyboardEvent | { metaKey: boolean; ctrlKey: boolean }) {
  return isMac ? event.metaKey : event.ctrlKey
}

/** 焦点在输入框 / 可编辑区域里时，单键快捷键（如 /）不能抢输入 */
export function isEditable(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

/** 页面上的主输入框（新建分析的问题框 / 对话页的追问框）带这个属性，按 / 时聚焦 */
export const PRIMARY_INPUT_ATTR = 'data-primary-input'

/**
 * 新建分析页的未提交输入。只存在 sessionStorage：切到别的页面再回来、刷新都还在，
 * 关掉标签页就丢掉，不会把半成品长期留在浏览器里。
 */

export interface NewSessionDraft {
  question: string
  logs: string[]
  code: string[]
  model: string
  since: string
  until: string
  timezone: string
  baseline: string
  encoding: string
}

const KEY = 'log-agent:new-session-draft'

export const EMPTY_DRAFT: NewSessionDraft = {
  question: '',
  logs: [],
  code: [],
  model: '',
  since: '',
  until: '',
  timezone: '',
  baseline: '',
  encoding: '',
}

export function readDraft(): NewSessionDraft {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return EMPTY_DRAFT
    const parsed = JSON.parse(raw) as Partial<NewSessionDraft>
    return {
      ...EMPTY_DRAFT,
      ...Object.fromEntries(
        Object.entries(parsed).filter(([k, v]) => k in EMPTY_DRAFT && typeof v === typeof EMPTY_DRAFT[k as keyof NewSessionDraft]),
      ),
      logs: Array.isArray(parsed.logs) ? parsed.logs.filter((p): p is string => typeof p === 'string') : [],
      code: Array.isArray(parsed.code) ? parsed.code.filter((p): p is string => typeof p === 'string') : [],
    }
  } catch {
    return EMPTY_DRAFT
  }
}

export function writeDraft(draft: NewSessionDraft) {
  try {
    const empty = Object.entries(draft).every(([, v]) => (Array.isArray(v) ? v.length === 0 : v === ''))
    if (empty) sessionStorage.removeItem(KEY)
    else sessionStorage.setItem(KEY, JSON.stringify(draft))
  } catch {
    // 隐私模式下写不了，只是不保留草稿
  }
}

export function clearDraft() {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // 同上
  }
}

// 路由层只依赖这个小模块，Workspace 页面本身才能被拆成按需加载的 chunk。
export type Panel = 'report' | 'source' | 'timeline'

export interface WorkspaceSearch {
  turn?: number
  src?: string
  start?: number
  end?: number
  ev?: string
  panel?: Panel
  /** 时间线要定位到的时间点（`HH:MM:SS` 或 `YYYY-MM-DDTHH:MM:SS`） */
  at?: string
}

export function validateWorkspaceSearch(search: Record<string, unknown>): WorkspaceSearch {
  const num = (v: unknown) => {
    const n = Number(v)
    return Number.isInteger(n) && n > 0 ? n : undefined
  }
  const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)
  const panel = search.panel === 'report' || search.panel === 'source' || search.panel === 'timeline' ? search.panel : undefined
  return {
    turn: num(search.turn),
    src: str(search.src),
    start: num(search.start),
    end: num(search.end),
    ev: str(search.ev),
    panel,
    at: typeof search.at === 'string' && /^(\d{4}-\d{2}-\d{2}T)?\d{2}:\d{2}:\d{2}$/.test(search.at) ? search.at : undefined,
  }
}

// 路由层只依赖这个小模块，Workspace 页面本身才能被拆成按需加载的 chunk。
export type Panel = 'report' | 'source' | 'timeline'

export interface WorkspaceSearch {
  turn?: number
  src?: string
  start?: number
  end?: number
  ev?: string
  panel?: Panel
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
  }
}

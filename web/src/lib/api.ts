import type {
  ConnectionTestResult,
  Settings,
  SettingsUpdate,
  CreateSessionBody,
  FsGlob,
  FsListing,
  LiveRun,
  MemoryIndex,
  MemoryItem,
  MemoryKind,
  MemorySaveResult,
  SessionMemory,
  Meta,
  ModelList,
  Place,
  SessionDetail,
  SessionSummary,
  Share,
  SkillDetail,
  SkillSource,
  SourceContext,
  Timeline,
  TraceItem,
  TurnPayload,
} from './types'

/** 本人视图走 /api/sessions/<name>，分享链接走 /api/share/<token>，其余接口完全相同 */
export type Scope = { kind: 'owner'; name: string } | { kind: 'share'; token: string }

export const CSRF_HEADERS = { 'X-Log-Agent-Request': '1' }

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export function scopeBase(scope: Scope): string {
  return scope.kind === 'owner' ? `/api/sessions/${encodeURIComponent(scope.name)}` : `/api/share/${encodeURIComponent(scope.token)}`
}

export function scopeKey(scope: Scope): string[] {
  return scope.kind === 'owner' ? ['owner', scope.name] : ['share', scope.token]
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...init,
    headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') message = body.detail
    } catch {
      /* 非 JSON 错误页 */
    }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined) search.set(key, String(value))
  return search.toString()
}

export const api = {
  meta: () => request<Meta>('/api/meta'),
  sessions: (q = '') => request<SessionSummary[]>(`/api/sessions?${query({ q })}`),
  session: (scope: Scope) => request<SessionDetail>(scopeBase(scope)),
  turn: (scope: Scope, turn: number) => request<TurnPayload>(`${scopeBase(scope)}/turns/${turn}`),
  timeline: (scope: Scope, buckets: number) => request<Timeline>(`${scopeBase(scope)}/timeline?${query({ buckets })}`),
  source: (scope: Scope, source: string, start: number, end: number, before: number, after: number) =>
    request<SourceContext>(`${scopeBase(scope)}/source?${query({ source, start, end, before, after })}`),
  exportUrl: (scope: Scope, turn: number, format: 'markdown' | 'json', view = 'detailed') =>
    `${scopeBase(scope)}/export?${query({ turn, format, view })}`,
  shares: (name: string) => request<Share[]>(`/api/sessions/${encodeURIComponent(name)}/shares`),
  createShare: (name: string, ttlHours: number | null) =>
    request<Share>(`/api/sessions/${encodeURIComponent(name)}/shares`, {
      method: 'POST',
      headers: CSRF_HEADERS,
      body: JSON.stringify({ ttl_hours: ttlHours }),
    }),
  revokeShare: (name: string, id: string) =>
    request<{ revoked: string }>(`/api/sessions/${encodeURIComponent(name)}/shares/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: CSRF_HEADERS,
    }),
  chatUrl: (name: string) => `/api/sessions/${encodeURIComponent(name)}/chat`,
  chatStreamUrl: (name: string) => `/api/sessions/${encodeURIComponent(name)}/chat/stream`,
  liveRun: (name: string) => request<LiveRun>(`/api/sessions/${encodeURIComponent(name)}/chat/live`),
  stopChat: (name: string, runId: string | undefined) =>
    request<{ stopped: boolean; pending?: boolean; finished?: boolean }>(`/api/sessions/${encodeURIComponent(name)}/chat/stop`, {
      method: 'POST',
      headers: { ...CSRF_HEADERS, 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId ?? null }),
    }),
  fsList: (path: string, opts: { hidden?: boolean; q?: string; dirs?: boolean; sort?: 'name' | 'mtime' } = {}) =>
    request<FsListing>(
      `/api/fs/list?${query({
        path,
        hidden: opts.hidden ? 'true' : undefined,
        q: opts.q || undefined,
        dirs: opts.dirs ? 'true' : undefined,
        sort: opts.sort === 'mtime' ? 'mtime' : undefined,
      })}`,
    ),
  fsGlob: (pattern: string) => request<FsGlob>(`/api/fs/glob?${query({ pattern })}`),
  places: () => request<Place[]>('/api/fs/places'),
  models: () => request<ModelList>('/api/models'),
  trace: (session?: string, limit = 300) => request<TraceItem[]>(`/api/trace?${query({ session, limit })}`),
  createSession: (body: CreateSessionBody) =>
    request<SessionSummary>('/api/sessions', { method: 'POST', headers: CSRF_HEADERS, body: JSON.stringify(body) }),
  patchSession: (name: string, body: { title?: string; pinned?: boolean }) =>
    request<SessionSummary>(`/api/sessions/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      headers: CSRF_HEADERS,
      body: JSON.stringify(body),
    }),
  deleteSession: (name: string) =>
    request<{ deleted: boolean }>(`/api/sessions/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      headers: CSRF_HEADERS,
      // 可撤销删除在关页面时才提交，keepalive 保证卸载期间请求仍会发出
      keepalive: true,
    }),

  skills: () => request<{ sources: SkillSource[] }>('/api/skills'),
  skill: (source: string, name: string) => request<SkillDetail>(skillUrl(source, name)),
  createSkill: (source: string, name: string, content: string) =>
    request<SkillDetail>('/api/skills', { method: 'POST', headers: CSRF_HEADERS, body: JSON.stringify({ source, name, content }) }),
  saveSkill: (source: string, name: string, content: string) =>
    request<SkillDetail>(skillUrl(source, name), { method: 'PUT', headers: CSRF_HEADERS, body: JSON.stringify({ content }) }),
  deleteSkill: (source: string, name: string) =>
    request<{ deleted: string }>(skillUrl(source, name), { method: 'DELETE', headers: CSRF_HEADERS }),

  settings: () => request<Settings>('/api/settings'),
  saveSettings: (body: SettingsUpdate) =>
    request<Settings>('/api/settings', { method: 'PUT', headers: CSRF_HEADERS, body: JSON.stringify(body) }),
  testConnection: (body: { model?: string; base_url?: string; api_key?: string }) =>
    request<ConnectionTestResult>('/api/settings/test', { method: 'POST', headers: CSRF_HEADERS, body: JSON.stringify(body) }),

  memory: () => request<MemoryIndex>('/api/memory'),
  sessionMemory: (name: string) => request<SessionMemory>(`/api/sessions/${encodeURIComponent(name)}/memory`),
  addMemory: (body: { text: string; kind: MemoryKind; project: string | null; replace_id?: number; force?: boolean }) =>
    request<MemorySaveResult>('/api/memory', { method: 'POST', headers: CSRF_HEADERS, body: JSON.stringify(body) }),
  updateMemory: (id: number, text: string) =>
    request<MemoryItem>(`/api/memory/${id}`, { method: 'PATCH', headers: CSRF_HEADERS, body: JSON.stringify({ text }) }),
  deleteMemory: (id: number) => request<{ deleted: number }>(`/api/memory/${id}`, { method: 'DELETE', headers: CSRF_HEADERS }),
  acceptCandidate: (id: number, body: { text?: string; replace_id?: number; force?: boolean } = {}) =>
    request<MemorySaveResult>(`/api/memory/candidates/${id}/accept`, {
      method: 'POST',
      headers: CSRF_HEADERS,
      body: JSON.stringify(body),
    }),
  rejectCandidate: (id: number, permanent: boolean) =>
    request<{ rejected: number }>(`/api/memory/candidates/${id}/reject`, {
      method: 'POST',
      headers: CSRF_HEADERS,
      body: JSON.stringify({ permanent }),
    }),
}

function skillUrl(source: string, name: string): string {
  return `/api/skills/${encodeURIComponent(source)}/${encodeURIComponent(name)}`
}

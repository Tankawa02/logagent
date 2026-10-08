import type {
  CreateSessionBody,
  FsGlob,
  FsListing,
  Meta,
  ModelList,
  Place,
  SessionDetail,
  SessionSummary,
  Share,
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
  return scope.kind === 'owner'
    ? `/api/sessions/${encodeURIComponent(scope.name)}`
    : `/api/share/${encodeURIComponent(scope.token)}`
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
  fsList: (path: string, opts: { hidden?: boolean; q?: string; dirs?: boolean } = {}) =>
    request<FsListing>(
      `/api/fs/list?${query({
        path,
        hidden: opts.hidden ? 'true' : undefined,
        q: opts.q || undefined,
        dirs: opts.dirs ? 'true' : undefined,
      })}`,
    ),
  fsGlob: (pattern: string) => request<FsGlob>(`/api/fs/glob?${query({ pattern })}`),
  places: () => request<Place[]>('/api/fs/places'),
  models: () => request<ModelList>('/api/models'),
  trace: (session?: string, limit = 300) => request<TraceItem[]>(`/api/trace?${query({ session, limit })}`),
  createSession: (body: CreateSessionBody) =>
    request<SessionSummary>('/api/sessions', { method: 'POST', headers: CSRF_HEADERS, body: JSON.stringify(body) }),
  deleteSession: (name: string) =>
    request<{ deleted: boolean }>(`/api/sessions/${encodeURIComponent(name)}`, { method: 'DELETE', headers: CSRF_HEADERS }),
}

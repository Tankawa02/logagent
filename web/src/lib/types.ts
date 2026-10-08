// 与 src/log_agent/report.py（schema_version 2）和 web/app.py 返回结构一一对应

export type Assessment = 'finding' | 'clear' | 'unknown'
export type Confidence = 'high' | 'medium' | 'low'
export type EvidenceStatus = 'verified' | 'shifted' | 'mismatch' | 'unresolved'

export interface Evidence {
  source: string
  line_start: number
  line_end: number
  excerpt: string
}

export interface Hypothesis {
  explanation: string
  confidence: Confidence
  reasoning: string
}

export interface Issue {
  title: string
  symptoms: string
  impact: string
  evidence: Evidence[]
  root_cause_hypotheses: Hypothesis[]
  open_questions: string[]
  recommendations: string[]
  reproduction_conditions: string[]
  verification_steps: string[]
}

export interface Analysis {
  assessment: Assessment
  confidence: Confidence
  conclusion: string
  impact: string
  next_steps: string[]
  issues: Issue[]
  open_questions: string[]
}

export interface EvidenceItem {
  issue: number
  index: number
  source: string
  line_start: number
  line_end: number
  status: EvidenceStatus
  note: string
  actual_start: number | null
  actual_end: number | null
}

export interface EvidenceCheck {
  status: 'verified' | 'incomplete' | 'mismatch' | 'failed' | 'unverifiable'
  total: number
  verified: number
  shifted: number
  mismatch: number
  unresolved: number
  items: EvidenceItem[]
  error?: string
}

export interface ToolCall {
  name: string
  args: Record<string, unknown>
  summary: string
  failed: boolean
  seconds: number
  subagent: string
  note: string
  /** 相对本轮开始的秒数；旧记录没有 */
  started?: number | null
  /** 本轮结束时仍在运行；seconds 只量到结束那一刻 */
  incomplete?: boolean
}

export interface LlmCall {
  started: number
  seconds: number
  first_token: number | null
  /** 流中途断开时为 null（用量未知） */
  input: number | null
  output: number | null
  tool_calls: number | null
  model: string
  finish_reason: string
  incomplete?: boolean
}

export interface TraceItem {
  session: string
  title: string
  turn: number
  question: string
  model: string
  status: 'ok' | 'error' | 'interrupted'
  error: string | null
  generated_at: string | null
  /** 旧版恢复的轮次没有保存耗时与用量，为 null */
  elapsed_seconds: number | null
  usage: { input: number; output: number; total: number } | null
  tool_count: number
  failed_tools: number
  incomplete_tools: number
  tool_seconds: number
  tools: Record<string, number>
  llm_calls: number | null
  budget_hit: boolean
  legacy: boolean
}

export interface TurnPayload {
  turn: number
  schema_version?: number
  analysis: Analysis | null
  structured_status: 'valid' | 'invalid' | 'missing'
  evidence_check: EvidenceCheck | null
  generated_at: string | null
  question: string
  logs: string[]
  code: string[]
  settings: Record<string, unknown>
  model: string
  status: 'ok' | 'error' | 'interrupted'
  error: string | null
  summary: string | null
  confidence: string | null
  budget_hit?: boolean
  elapsed_seconds: number
  usage: { input?: number; output?: number; total?: number }
  tool_calls: ToolCall[]
  llm_calls?: LlmCall[]
  report: string
  provenance?: string
}

export interface TurnBrief {
  turn: number
  question: string
  generated_at: string | null
  status: string | null
  summary: string | null
  assessment: Assessment | null
  confidence: Confidence | null
  evidence_status: EvidenceCheck['status'] | null
  issues: number
}

export interface SourceRef {
  path: string
  name: string
  exists?: boolean
}

export interface SessionSummary {
  name: string
  /** analyze 单次分析存档 / chat 多轮对话 */
  origin: 'analyze' | 'chat'
  title: string
  logs: SourceRef[]
  code: SourceRef[]
  model: string
  turns: number
  total_tokens: number
  created_at: string
  updated_at: string
  settings: Record<string, unknown>
  last?: TurnBrief | null
}

export interface SessionDetail extends SessionSummary {
  read_only: boolean
  turn_list: TurnBrief[]
}

export interface Meta {
  version: string
  authenticated: boolean
  can_chat: boolean
  share_base: string
  loopback: boolean
  db: string | null
  default_model: string | null
}

export interface FsEntry {
  name: string
  path: string
  kind: 'dir' | 'file'
  size: number | null
  mtime: number
}

export interface FsListing {
  path: string
  parent: string | null
  sep: string
  /** 服务端已应用的筛选词（小写） */
  query?: string
  entries: FsEntry[]
  /** 筛选后的条目总数，可能大于 entries.length */
  total?: number
  truncated: boolean
}

/** 服务端正在跑（或刚跑完）的一轮，和浏览器连接无关 */
export interface LiveRun {
  active: boolean
  question?: string
  run_id?: string
  started?: number
  finished_at?: number | null
}

export interface FsGlob {
  files: string[]
  dir: string
  truncated: boolean
}

export interface Place {
  label: string
  path: string
  kind: 'home' | 'cwd' | 'recent-log' | 'recent-code' | 'drive' | 'root'
}

export interface ModelList {
  default: string
  models: string[]
}

export interface CreateSessionBody {
  logs: string[]
  code: string[]
  model?: string
  since?: string
  until?: string
  timezone?: string
  baseline?: string
  encoding?: string
}

export interface LineRef {
  source: string
  line: number
}

export interface Bucket {
  index: number
  start: string
  end: string
  start_ts: number
  total: number
  warn: number
  error: number
  first: LineRef[]
  first_error: LineRef[]
  top: { signature: string; count: number; source: string; line: number }[]
  spike?: boolean
}

export interface Timeline {
  timezone: string
  time_only: boolean
  bucket_seconds: number
  start?: string
  end?: string
  buckets: Bucket[]
  spikes: number[]
  threshold: number
  totals: { events: number; warn: number; error: number }
  files: { source: string; name: string; events?: number; first?: string | null; last?: string | null; error?: string }[]
}

export interface SourceContext {
  kind: 'log' | 'code'
  source: string
  name: string
  path: string
  line_start: number
  line_end: number
  first: number
  last: number
  total_lines: number | null
  has_more: boolean
  lines: { n: number; text: string }[]
}

export interface Share {
  id: string
  name: string
  hint: string
  created_at: string
  expires_at: string | null
  url?: string
  token?: string
}

/** 右侧原文面板要打开的位置 */
export interface SourceTarget {
  source: string
  start: number
  end: number
  /** 来自哪条证据（issue-index），用于高亮左侧卡片 */
  evidenceKey?: string
}

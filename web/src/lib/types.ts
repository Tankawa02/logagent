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
  /** 返回给模型的原文（截断到上限）；旧记录没有 */
  output?: string
  output_chars?: number | null
  call_id?: string
}

/** 截断存档的文本：chars 是原始长度，大于 text.length 表示被截断 */
export interface ClippedText {
  text: string
  chars: number
}

export interface ToolRequest {
  name: string
  args: Record<string, unknown>
  id: string
}

export interface TraceMessage extends ClippedText {
  role: 'system' | 'user' | 'assistant' | 'tool' | string
  name?: string
  tool_call_id?: string
  tool_requests?: ToolRequest[]
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
  /** 以下为 trace 明细，旧记录没有 */
  input_messages?: TraceMessage[]
  input_full?: boolean
  input_count?: number
  output_text?: ClippedText
  reasoning?: ClippedText
  tool_requests?: ToolRequest[]
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
  usage: { input: number; output: number; total: number; cache_read?: number } | null
  /** 本轮费用（美元）；价格未知或旧会话为 null */
  cost_usd?: number | null
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
  usage: Usage
  tool_calls: ToolCall[]
  llm_calls?: LlmCall[]
  report: string
  provenance?: string
  /** inline：模型在正文末尾写的附录；extracted：正文写完后单独抽取 */
  structured_source?: 'inline' | 'extracted' | null
  evidence_repair?: EvidenceRepair | null
  cost?: TurnCost | null
  similar_cases?: SimilarCase[]
}

export interface Usage {
  input?: number
  output?: number
  total?: number
  cache_read?: number
  cache_write?: number
  reasoning?: number
}

export interface TurnCost {
  usd: number | null
  complete: boolean
  unpriced_tokens?: number
  sources?: string[]
}

export interface EvidenceRepair {
  adopted: boolean
  shifted_fixed?: number
  relocated?: number
  model_fixed?: number
  dropped?: number
  error?: string
}

export interface SimilarCase {
  session: string
  turn: number
  title: string
  conclusion: string
  confidence: string
  created_at: string
  matched: string[]
  correction: string
  confirmed: boolean
  same_project: boolean
}

export type FeedbackRating = 'up' | 'down' | 'wrong'

export interface TurnFeedback {
  rating: FeedbackRating
  comment: string
  created_at: string
}

export interface TurnBrief {
  /** 本人视图才有：这一轮的回答反馈 */
  feedback?: TurnFeedback | null
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
  /** 只在会话列表里返回 */
  pinned?: boolean
  /** 只在会话列表里返回：已记录的费用合计（美元），没有任何一轮能计价时为 null */
  cost_usd?: number | null
  /** 来源 / 范围在会话中途改过、还没在下一次提问里告知模型 */
  pending_change?: boolean
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

export type SettingKey = 'model' | 'base_url' | 'timeout' | 'max_retries' | 'timezone' | 'memory'
export type SettingSource = 'default' | 'user' | 'user_section' | 'project' | 'env' | 'cli'
export type SettingValue = string | number | null

export interface SettingField {
  /** 用户级 config.toml 顶层的值；网页保存的就是它 */
  value: SettingValue
  effective: SettingValue
  default: SettingValue
  source: SettingSource
  origin: string | null
  locked: boolean
  env: string | null
}

export interface Settings {
  config_path: string
  credentials_path: string
  project_config: string | null
  read_only: boolean
  can_chat: boolean
  api_key: { set: boolean; masked: string | null; source: 'env' | 'file' | null; locked: boolean; saved: boolean }
  fields: Record<SettingKey, SettingField>
}

export interface SettingsUpdate {
  values: Partial<Record<SettingKey, SettingValue>>
  api_key?: string
  clear_api_key?: boolean
}

export interface ConnectionTestResult {
  ok: boolean
  message: string
  latency: number
  served_model?: string
  model: string
  endpoint: string
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
  /** 这一轮存档后的轮次号；会话历史里已有它就说明已经存档，不用再接 */
  turn?: number | null
  saved_turn?: number | null
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

export interface SessionSourcesBody {
  logs: string[]
  code: string[]
  since?: string
  until?: string
  baseline?: string
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

export interface SkillSummary {
  name: string
  description: string
  /** 非空时 deepagents 会跳过这本手册 */
  problems: string[]
  /** 被更高优先级来源里的同名 skill 覆盖时，为那个来源的名称 */
  shadowed_by: string | null
  files: number
  updated_at: string | null
  /** 符号链接��� skill：只能查看，需在链接目标处编辑 */
  readonly: boolean
}

export interface SkillSource {
  key: string
  label: string
  hint: string
  directory: string
  exists: boolean
  skills: SkillSummary[]
}

export interface SkillDetail {
  source: string
  source_label: string
  name: string
  path: string
  content: string
  problems: string[]
  files: string[]
  updated_at: string | null
  readonly: boolean
}

export type MemoryKind = 'preference' | 'term' | 'fact'

export interface MemoryItem {
  id: number
  kind: MemoryKind
  kind_label: string
  project: string | null
  scope_label: string
  text: string
  origin: string
  created_at: string
  updated_at: string
}

export interface MemoryCandidate {
  id: number
  kind: MemoryKind
  kind_label: string
  project: string | null
  scope_label: string
  text: string
  signal: string
  reason: string
  occurrences: number
  first_seen: string
  last_seen: string
}

export interface MemoryIndex {
  path: string
  kinds: { key: MemoryKind; label: string }[]
  projects: { key: string; label: string }[]
  memories: MemoryItem[]
  pending: MemoryCandidate[]
}

/** 对话页：本会话每轮带上的记忆（全局 + 本项目），以及本会话里提出、等确认的候选 */
export interface SessionMemory {
  mode: 'suggest' | 'explicit' | 'off'
  project: string | null
  project_label: string
  available: boolean
  /** 每轮实际注入提示词的记忆 */
  memories: MemoryItem[]
  /** 超出提示词篇幅预算、本轮没有带上的记忆 */
  skipped: MemoryItem[]
  pending: MemoryCandidate[]
}

/** 发现相似记忆时不保存，返回相似项让用户选择新增还是替换 */
export type MemorySaveResult = { saved: true; updated?: boolean; memory: MemoryItem } | { saved: false; similar: MemoryItem[] }

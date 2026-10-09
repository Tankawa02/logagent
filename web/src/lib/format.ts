import type { Assessment, Confidence, EvidenceCheck, EvidenceStatus } from './types'

/** 与 report.visible_report 一致：隐藏机器可读附录（包括还在流式输出中的半个围栏） */
export function visibleReport(text: string): string {
  const match = /^```log-agent-report\s*$/m.exec(text)
  return match ? text.slice(0, match.index).trimEnd() : text
}

/**
 * 时间线返回的是带偏移的 ISO 字符串（按会话时区）。直接截取文本显示，
 * 不经过浏览器时区换算，保证和日志原文里的时间对得上。
 */
export function stampParts(iso: string): { date: string; time: string } {
  const [date, rest = ''] = iso.split('T')
  return { date, time: rest.slice(0, 8) }
}

export function formatRange(start: string, end: string, timeOnly: boolean): string {
  const a = stampParts(start)
  const b = stampParts(end)
  const head = timeOnly ? a.time : `${a.date} ${a.time}`
  return a.date === b.date || timeOnly ? `${head} ~ ${b.time}` : `${head} ~ ${b.date} ${b.time}`
}

export function formatWidth(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${seconds / 60} 分钟`
  if (seconds < 86400) return `${seconds / 3600} 小时`
  return `${seconds / 86400} 天`
}

export function formatDuration(seconds: number): string {
  if (!seconds) return '0s'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  return `${m}m${Math.round(seconds - m * 60)}s`
}

export function formatTokens(value: number | undefined | null): string {
  const n = value ?? 0
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}

/** 去掉 provider 前缀，列表里更紧凑 */
export function shortModel(model: string): string {
  return model.replace(/^[a-z_-]+:/i, '')
}

export const TURN_STATUS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: '完成', tone: 'green' },
  error: { label: '出错', tone: 'red' },
  interrupted: { label: '已中断', tone: 'amber' },
}

export function formatGenerated(value: string | null | undefined): string {
  if (!value) return '时间未知'
  return value.replace('T', ' ').replace(/(\.\d+)?([+-]\d{2}:\d{2}|Z)$/, '')
}

export function baseName(path: string): string {
  return path.split(/[\\/]/).pop() || path
}

export const ASSESSMENT: Record<Assessment, { label: string; tone: Tone }> = {
  finding: { label: '发现问题', tone: 'red' },
  clear: { label: '未发现问题', tone: 'green' },
  unknown: { label: '无法判定', tone: 'gray' },
}

export const CONFIDENCE: Record<Confidence, string> = { high: '高', medium: '中', low: '低' }

export const EVIDENCE: Record<EvidenceStatus, { label: string; tone: Tone; mark: string }> = {
  verified: { label: '已核对原文', tone: 'green', mark: '✓' },
  shifted: { label: '行号偏移', tone: 'amber', mark: '△' },
  mismatch: { label: '与原文不符', tone: 'red', mark: '✗' },
  unresolved: { label: '无法核对', tone: 'gray', mark: '?' },
}

export const CHECK_STATUS: Record<EvidenceCheck['status'], { label: string; tone: Tone }> = {
  verified: { label: '证据全部核对通过', tone: 'green' },
  incomplete: { label: '部分证据无法核对', tone: 'amber' },
  mismatch: { label: '有证据与原文不符', tone: 'amber' },
  failed: { label: '证据均与原文不符', tone: 'amber' },
  unverifiable: { label: '证据无法核对', tone: 'gray' },
}

/** 把路径拆成目录和文件名：长路径展示时文件名完整保留，目录部分可以截断或弱化 */
export function splitPath(path: string): { dir: string; name: string } {
  const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return cut < 0 ? { dir: '', name: path } : { dir: path.slice(0, cut + 1), name: path.slice(cut + 1) }
}

export type Tone = 'red' | 'amber' | 'green' | 'gray' | 'blue'

export const TONE_CLASS: Record<Tone, string> = {
  // 红色只留给「确认故障 / 出错」，做成实心让它成为唯一的强警示；证据层面的问题用琥珀色
  red: 'bg-red-600 text-white ring-red-600 dark:bg-red-500/90 dark:text-white dark:ring-red-500/90',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900',
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-900',
  gray: 'bg-zinc-100 text-zinc-600 ring-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-zinc-700',
  blue: 'bg-brand-50 text-brand-700 ring-brand-200 dark:bg-brand-950/40 dark:text-brand-300 dark:ring-brand-900',
}

/** 报告里反引号包着的 `文件:行号` / `文件:起-止`，与 citations._CITATION 相同的写法 */
export const CITATION = /^([^`\s:]+):(\d+)(?:\s*[-–~]\s*(\d+))?$/

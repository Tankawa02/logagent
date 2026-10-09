import type { TurnPayload } from './types'

const GENERIC = ['这些失败的请求有什么共同点？', '给出修复建议和验证步骤', '还有没有被忽略的其他异常？']

const MAX = 3

function clip(text: string, length = 36): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > length ? `${flat.slice(0, length - 1)}…` : flat
}

/**
 * 根据上一轮的结构化报告给出追问：优先追问还没确认的根因和未决问题，
 * 再落到修复验证；报告缺失或没有结构化结果时退回通用追问。
 */
export function followUpsFor(payload: TurnPayload | undefined | null): string[] {
  const analysis = payload?.analysis
  if (!analysis) return GENERIC
  const result: string[] = []
  const push = (text: string) => {
    if (result.length < MAX && !result.includes(text)) result.push(text)
  }

  for (const issue of analysis.issues) {
    const weak = issue.root_cause_hypotheses.find((h) => h.confidence !== 'high')
    if (weak) push(`「${clip(issue.title, 24)}」的根因还能怎么进一步确认？`)
    if (issue.open_questions[0]) push(clip(issue.open_questions[0], 48))
  }
  for (const question of analysis.open_questions) push(clip(question, 48))
  const issue = analysis.issues[0]
  if (issue && issue.verification_steps.length === 0) push(`针对「${clip(issue.title, 24)}」给出修复建议和验证步骤`)
  if (analysis.next_steps[0]) push(`展开说说：${clip(analysis.next_steps[0])}`)
  if (analysis.assessment === 'clear') push('还有没有被忽略的其他异常？')
  for (const fallback of GENERIC) push(fallback)
  return result
}

// 新建分析页创建会话后跳到会话页，由会话页的对话面板接着把第一个问题发出去。
// 只在内存里交接：刷新页面后不会重复提问。
// 交接只对「这一次跳转」有效：过了有效期、或会话页没能打开时就作废，
// 以后再进这个会话不会突然把旧问题发出去。
const HANDOFF_TTL_MS = 15_000

const pending = new Map<string, { question: string; at: number }>()

export function setPendingQuestion(session: string, question: string) {
  pending.set(session, { question, at: Date.now() })
}

export function takePendingQuestion(session: string): string | undefined {
  const entry = pending.get(session)
  pending.delete(session)
  if (!entry || Date.now() - entry.at > HANDOFF_TTL_MS) return undefined
  return entry.question
}

export function discardPendingQuestion(session: string) {
  pending.delete(session)
}

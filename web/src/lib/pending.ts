// 新建分析页创建会话后跳到会话页，由会话页的对话面板接着把第一个问题发出去。
// 只在内存里交接：刷新页面后不会重复提问。
const pending = new Map<string, string>()

export function setPendingQuestion(session: string, question: string) {
  pending.set(session, question)
}

export function takePendingQuestion(session: string): string | undefined {
  const question = pending.get(session)
  pending.delete(session)
  return question
}

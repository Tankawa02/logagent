// 新建分析页创建会话后跳到会话页，由会话页的对话面板接着把第一个问题发出去。
// 只在内存里交接：刷新页面后不会重复提问。
// 交接跟着「这一次跳转」走，而不是固定时限：会话页还在加载就一直保留，
// 用户离开会话页（或会话页打不开 / 不能提问）就作废，以后再进这个会话不会突然把旧问题发出去。

const pending = new Map<string, string>()
const discardTimers = new Map<string, ReturnType<typeof setTimeout>>()

function cancelDiscard(session: string) {
  const timer = discardTimers.get(session)
  if (timer !== undefined) {
    clearTimeout(timer)
    discardTimers.delete(session)
  }
}

export function setPendingQuestion(session: string, question: string) {
  cancelDiscard(session)
  pending.set(session, question)
}

export function takePendingQuestion(session: string): string | undefined {
  const question = pending.get(session)
  pending.delete(session)
  return question
}

export function discardPendingQuestion(session: string) {
  cancelDiscard(session)
  pending.delete(session)
}

/**
 * 会话页挂载期间保留交接，返回的清理函数在页面卸载时作废它。
 * 作废推迟一拍：StrictMode 开发模式下的「假卸载」会被紧接着的重新挂载取消。
 */
export function holdPendingQuestion(session: string): () => void {
  cancelDiscard(session)
  return () => {
    cancelDiscard(session)
    discardTimers.set(
      session,
      setTimeout(() => {
        discardTimers.delete(session)
        pending.delete(session)
      }, 0),
    )
  }
}

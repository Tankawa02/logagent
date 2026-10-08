import { fetchServerSentEvents, type ConnectConnectionAdapter } from '@tanstack/ai-react'
import { api, CSRF_HEADERS } from './api'

type Chunk = ReturnType<ConnectConnectionAdapter['connect']> extends AsyncIterable<infer C> ? C : never

/** 读服务端的 SSE：`data: {json}` 一条一个事件，`: keep-alive` 之类的注释行忽略 */
async function* readServerSentEvents(response: Response): AsyncGenerator<Chunk> {
  if (!response.body) return
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) yield JSON.parse(data) as Chunk
      boundary = buffer.indexOf('\n\n')
    }
  }
}

export interface LiveChatConnection extends ConnectConnectionAdapter {
  /** 下一次 connect 改为接回服务端正在跑的那一轮（重放已有事件 + 跟随后续），而不是发起新的一轮 */
  resumeNext: (runId: string | undefined) => void
  /** 当前（或最近一次）连接对应的 run_id，停止时用它指明要停哪一轮 */
  currentRunId: () => string | undefined
}

function newRunId() {
  return `run-${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`
}

/**
 * 分析在服务端独立于浏览器连接运行：切走再切回来时，用 resumeNext + reload() 接回原来那一轮。
 * 正常提问仍走 TanStack AI 自带的 fetchServerSentEvents。
 */
export function liveChatConnection(session: string): LiveChatConnection {
  const post = fetchServerSentEvents(api.chatUrl(session), { headers: CSRF_HEADERS })
  let resume = false
  let runId: string | undefined
  return {
    resumeNext: (id) => {
      resume = true
      runId = id
    },
    currentRunId: () => runId,
    connect(messages, data, abortSignal, runContext) {
      if (!resume) {
        // run_id 由浏览器先定好：请求还没到服务端时点停止，服务端也知道要停的是哪一轮
        runId = runContext?.runId ?? newRunId()
        // threadId 缺省时和 TanStack 自己的行为一致：随机生成
        const threadId = runContext?.threadId ?? `thread-${crypto.randomUUID()}`
        return post.connect(messages, data, abortSignal, { ...runContext, threadId, runId })
      }
      resume = false
      return (async function* () {
        const response = await fetch(api.chatStreamUrl(session), {
          credentials: 'same-origin',
          headers: { Accept: 'text/event-stream' },
          signal: abortSignal,
        })
        if (!response.ok) throw new Error(response.status === 404 ? '这一轮已经结束' : `${response.status} ${response.statusText}`)
        yield* readServerSentEvents(response)
      })()
    },
  }
}

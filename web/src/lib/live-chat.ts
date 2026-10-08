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
  resumeNext: () => void
}

/**
 * 分析在服务端独立于浏览器连接运行：切走再切回来时，用 resumeNext + reload() 接回原来那一轮。
 * 正常提问仍走 TanStack AI 自带的 fetchServerSentEvents。
 */
export function liveChatConnection(session: string): LiveChatConnection {
  const post = fetchServerSentEvents(api.chatUrl(session), { headers: CSRF_HEADERS })
  let resume = false
  return {
    resumeNext: () => {
      resume = true
    },
    connect(messages, data, abortSignal, runContext) {
      if (!resume) return post.connect(messages, data, abortSignal, runContext)
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

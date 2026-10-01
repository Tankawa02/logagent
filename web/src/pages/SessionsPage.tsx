import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Badge, Card, Empty, ErrorBox, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { ASSESSMENT, CHECK_STATUS } from '../lib/format'

export function SessionsPage() {
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setQ(text.trim()), 250)
    return () => clearTimeout(timer)
  }, [text])

  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const sessions = useQuery({
    queryKey: ['sessions', q],
    queryFn: () => api.sessions(q),
    enabled: !!meta.data?.authenticated,
    placeholderData: keepPreviousData,
  })

  if (meta.data && !meta.data.authenticated) return <AuthRequired />

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-3 py-6 sm:px-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">log-agent 会话</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            来自 <span className="font-mono">{meta.data?.db ?? '…'}</span>，用 <code>log-agent chat</code> 产生的会话都会出现在这里。
          </p>
        </div>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="搜索会话名、日志路径、问题或回答"
          className="w-full rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm outline-none focus:border-gray-500 sm:w-80 dark:border-gray-700 dark:bg-gray-900"
        />
      </header>
      {sessions.error && <ErrorBox error={sessions.error} />}
      <Card>
        {(meta.isLoading || sessions.isLoading) && (
          <div className="flex justify-center p-10">
            <Spinner />
          </div>
        )}
        {sessions.data && sessions.data.length === 0 && (
          <Empty>{q ? `没有匹配「${q}」的会话。` : '还没有会话。先运行 log-agent chat -l app.log 排查一次，再刷新这里。'}</Empty>
        )}
        <ul className="divide-y divide-gray-100 dark:divide-gray-800">
          {sessions.data?.map((s) => (
            <li key={s.name}>
              <Link
                to="/sessions/$name"
                params={{ name: s.name }}
                className="block px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/50"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-gray-900 dark:text-gray-50">{s.title || s.name}</span>
                  {s.last?.assessment && (
                    <Badge tone={ASSESSMENT[s.last.assessment].tone}>{ASSESSMENT[s.last.assessment].label}</Badge>
                  )}
                  {s.last?.evidence_status && (
                    <Badge tone={CHECK_STATUS[s.last.evidence_status].tone}>{CHECK_STATUS[s.last.evidence_status].label}</Badge>
                  )}
                  <span className="ml-auto text-xs text-gray-500">{s.updated_at}</span>
                </div>
                {s.last?.summary && (
                  <p className="mt-1 line-clamp-2 text-sm text-gray-600 dark:text-gray-300">{s.last.summary}</p>
                )}
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-500">
                  <span className="font-mono">{s.name}</span>
                  <span>· {s.turns} 轮</span>
                  <span>· {s.total_tokens.toLocaleString()} tokens</span>
                  {s.logs.map((log) => (
                    <Badge key={log.path} tone={log.exists === false ? 'red' : 'gray'} title={log.path}>
                      {log.name}
                    </Badge>
                  ))}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

export function AuthRequired() {
  return (
    <div className="mx-auto max-w-lg p-8">
      <Card className="space-y-2 p-6">
        <h1 className="text-lg font-semibold">需要访问令牌</h1>
        <p className="text-sm text-gray-600 dark:text-gray-300">
          请使用 <code>log-agent serve</code> 启动时终端里打印的完整链接（带 <code>?token=</code>）打开。
          同事交接请让会话所有者在会话页点「分享」生成只读链接。
        </p>
      </Card>
    </div>
  )
}

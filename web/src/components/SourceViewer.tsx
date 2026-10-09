import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, scopeKey, type Scope } from '../lib/api'
import { splitPath } from '../lib/format'
import type { SourceTarget } from '../lib/types'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Spinner } from './ui'

const STEP = 50
const MAX_CONTEXT = 200 // Keep in sync with web/sources.py.

export function SourceViewer({ scope, target }: { scope: Scope; target: SourceTarget | null }) {
  const [before, setBefore] = useState(15)
  const [after, setAfter] = useState(15)
  const [copied, setCopied] = useState(false)
  const highlight = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)

  // 换一处引用时上下文恢复默认范围
  const key = target ? `${target.source}:${target.start}-${target.end}` : ''
  useEffect(() => {
    setBefore(15)
    setAfter(15)
  }, [key])

  const query = useQuery({
    queryKey: [...scopeKey(scope), 'source', key, before, after],
    queryFn: () => api.source(scope, target!.source, target!.start, target!.end, before, after),
    enabled: !!target,
    placeholderData: keepPreviousData,
    retry: false,
  })

  // 只在切换引用时滚动到高亮行；加载更多上下文时保持当前位置
  const scrolledFor = useRef('')
  useEffect(() => {
    const line = highlight.current
    const box = scroller.current
    if (query.data && !query.isPlaceholderData && scrolledFor.current !== key && line && box) {
      // 宽屏左右对照时原文面板是固定高度的独立滚动区：只滚面板，不带动整页；窄屏上下排列时把面板滚进视野
      if (window.matchMedia('(min-width: 1024px)').matches) {
        box.scrollTo({ top: line.offsetTop - box.clientHeight / 2 + line.clientHeight / 2, behavior: 'smooth' })
      } else {
        line.scrollIntoView({ block: 'center', behavior: 'smooth' })
      }
      scrolledFor.current = key
    }
  }, [query.data, query.isPlaceholderData, key])

  if (!target) {
    return (
      <Card className="h-full">
        <CardHeader title="原文" />
        <Empty>
          点击左侧证据、报告里的 <code className="font-mono">文件:行号</code> 引用，或时间线里的错误，
          <br />
          在这里查看日志 / 源码原文和上下文。
        </Empty>
      </Card>
    )
  }

  const data = query.data
  const range = `${target.start}${target.end !== target.start ? `-${target.end}` : ''}`
  const ref = `${target.source}:${range}`
  const { dir, name } = splitPath(target.source)

  return (
    <Card className="flex h-full min-h-0 flex-col">
      <CardHeader
        fill
        title={
          <span className="flex min-w-0 items-center gap-2" title={data?.path ?? ref}>
            {data && (
              <span className="shrink-0">
                <Badge tone={data.kind === 'log' ? 'blue' : 'gray'}>{data.kind === 'log' ? '日志' : '源码'}</Badge>
              </span>
            )}
            {/* 目录过长时截断，文件名和行号始终完整显示 */}
            <span className="flex min-w-0 font-mono text-xs font-normal">
              {dir && <span className="truncate text-zinc-400">{dir}</span>}
              <span className="shrink-0 font-semibold">
                {name}:{range}
              </span>
            </span>
            {query.isFetching && <Spinner />}
          </span>
        }
      >
        <Button
          variant="ghost"
          className="text-xs"
          onClick={() => {
            void navigator.clipboard?.writeText(ref).then(() => {
              setCopied(true)
              setTimeout(() => setCopied(false), 1200)
            })
          }}
        >
          {copied ? '已复制' : '复制引用'}
        </Button>
      </CardHeader>
      {query.error && (
        <div className="p-4">
          <ErrorBox error={query.error} />
        </div>
      )}
      {data && (
        <div ref={scroller} className="relative min-h-0 flex-1 overflow-auto">
          {data.first > 1 && before < MAX_CONTEXT && (
            <LoadMore
              onClick={() => setBefore((v) => Math.min(v + STEP, MAX_CONTEXT))}
              label={`向上加载 ${STEP} 行（从第 ${data.first} 行起）`}
            />
          )}
          <div className="font-mono text-xs leading-5">
            {data.lines.map(({ n, text }) => {
              const marked = n >= data.line_start && n <= data.line_end
              return (
                <div
                  key={n}
                  ref={n === data.line_start ? highlight : undefined}
                  className={`flex ${marked ? 'bg-amber-100/80 dark:bg-amber-900/30' : 'hover:bg-zinc-50 dark:hover:bg-zinc-800/50'}`}
                >
                  <span
                    className={`w-14 shrink-0 select-none border-r px-2 text-right tabular-nums ${marked ? 'border-amber-400 text-amber-700 dark:text-amber-300' : 'border-zinc-100 text-zinc-400 dark:border-zinc-800'}`}
                  >
                    {n}
                  </span>
                  <span className="whitespace-pre-wrap break-all px-3 text-zinc-800 dark:text-zinc-200">{text || ' '}</span>
                </div>
              )
            })}
          </div>
          {data.has_more && after < MAX_CONTEXT && (
            <LoadMore onClick={() => setAfter((v) => Math.min(v + STEP, MAX_CONTEXT))} label={`向下加载 ${STEP} 行`} />
          )}
          <p className="border-t border-zinc-100 px-3 py-1.5 text-xs text-zinc-500 dark:text-zinc-400 dark:border-zinc-800">
            {data.path}
            {data.total_lines ? ` · 共 ${data.total_lines.toLocaleString()} 行` : ''}
          </p>
        </div>
      )}
    </Card>
  )
}

function LoadMore({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="block w-full border-y border-dashed border-zinc-200 py-1 text-center text-xs text-zinc-500 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800"
    >
      {label}
    </button>
  )
}

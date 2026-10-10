import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Search, WrapText } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, scopeKey, type Scope } from '../lib/api'
import { splitPath } from '../lib/format'
import { useCopy } from '../lib/hooks'
import type { SourceTarget } from '../lib/types'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Spinner } from './ui'

const STEP = 50
const MAX_CONTEXT = 200 // Keep in sync with web/sources.py.

export function SourceViewer({ scope, target }: { scope: Scope; target: SourceTarget | null }) {
  const [before, setBefore] = useState(15)
  const [after, setAfter] = useState(15)
  const { state, copy } = useCopy()
  const [wrap, setWrap] = useState(true)
  const [finding, setFinding] = useState(false)
  const [needle, setNeedle] = useState('')
  const [cursor, setCursor] = useState(0)
  const highlight = useRef<HTMLDivElement | null>(null)
  const currentMatch = useRef<HTMLDivElement | null>(null)
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

  const lines = query.data?.lines
  const matches = useMemo(() => {
    const q = needle.trim().toLowerCase()
    if (!q || !lines) return []
    return lines.filter((l) => l.text.toLowerCase().includes(q)).map((l) => l.n)
  }, [needle, lines])

  useEffect(() => {
    const line = currentMatch.current
    const box = scroller.current
    if (!matches.length || !line || !box) return
    box.scrollTo({ top: line.offsetTop - box.clientHeight / 2, behavior: 'smooth' })
  }, [matches, cursor])

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
        <button
          type="button"
          onClick={() => setFinding((v) => !v)}
          aria-pressed={finding}
          aria-label="在原文中查找"
          title="在已加载的原文中查找"
          className={`rounded-lg p-1.5 transition-colors ${finding ? 'bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50' : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'}`}
        >
          <Search className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          onClick={() => setWrap((v) => !v)}
          aria-pressed={wrap}
          aria-label="自动换行"
          title={wrap ? '自动换行：开' : '自动换行：关'}
          className={`rounded-lg p-1.5 transition-colors ${wrap ? 'bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50' : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'}`}
        >
          <WrapText className="h-3.5 w-3.5" aria-hidden />
        </button>
        <Button variant="ghost" className="text-xs" onClick={() => void copy(ref)}>
          {state === 'copied' ? '已复制' : state === 'failed' ? '复制失败，请手动选中' : '复制引用'}
        </Button>
      </CardHeader>
      {finding && (
        <div className="flex items-center gap-2 border-b border-zinc-100 px-3 py-1.5 dark:border-zinc-800">
          <input
            autoFocus
            type="search"
            value={needle}
            onChange={(e) => {
              setNeedle(e.target.value)
              setCursor(0)
            }}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || e.keyCode === 229) return
              if (e.key === 'Enter' && matches.length) {
                e.preventDefault()
                setCursor((c) => (c + (e.shiftKey ? -1 : 1) + matches.length) % matches.length)
              } else if (e.key === 'Escape') {
                e.preventDefault()
                e.stopPropagation()
                setFinding(false)
                setNeedle('')
              }
            }}
            placeholder="查找（Enter 下一个，Shift+Enter 上一个）"
            aria-label="在原文中查找"
            className="min-w-0 flex-1 bg-transparent font-mono text-xs text-zinc-800 outline-none placeholder:font-sans placeholder:text-zinc-400 dark:text-zinc-200"
          />
          <span className="shrink-0 text-xs tabular-nums text-zinc-500 dark:text-zinc-400" aria-live="polite">
            {needle ? (matches.length ? `${cursor + 1}/${matches.length}` : '无匹配') : ''}
          </span>
        </div>
      )}
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
          <div className={`font-mono text-xs leading-5 ${wrap ? '' : 'w-max min-w-full'}`}>
            {data.lines.map(({ n, text }) => {
              const marked = n >= data.line_start && n <= data.line_end
              const current = matches[cursor] === n
              return (
                <div
                  key={n}
                  ref={(node) => {
                    if (n === data.line_start) highlight.current = node
                    if (current) currentMatch.current = node
                  }}
                  className={`flex ${marked ? 'bg-amber-100/80 dark:bg-amber-900/30' : 'hover:bg-zinc-50 dark:hover:bg-zinc-800/50'} ${current ? 'outline outline-1 -outline-offset-1 outline-brand-400' : ''}`}
                >
                  <span
                    className={`sticky left-0 w-14 shrink-0 select-none border-r px-2 text-right tabular-nums ${marked ? 'border-amber-400 bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300' : 'border-zinc-100 bg-white text-zinc-400 dark:border-zinc-800 dark:bg-zinc-900'}`}
                  >
                    {n}
                  </span>
                  <span className={`px-3 text-zinc-800 dark:text-zinc-200 ${wrap ? 'whitespace-pre-wrap break-all' : 'whitespace-pre'}`}>
                    {text ? <Highlighted text={text} needle={needle} /> : ' '}
                  </span>
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

function Highlighted({ text, needle }: { text: string; needle: string }) {
  const q = needle.trim()
  if (!q) return <>{text}</>
  const lower = text.toLowerCase()
  const target = q.toLowerCase()
  const parts: ReactNode[] = []
  let from = 0
  let at = lower.indexOf(target)
  while (at !== -1) {
    if (at > from) parts.push(text.slice(from, at))
    parts.push(
      <mark key={at} className="rounded-sm bg-yellow-300/80 text-inherit dark:bg-yellow-500/40">
        {text.slice(at, at + q.length)}
      </mark>,
    )
    from = at + q.length
    at = lower.indexOf(target, from)
  }
  if (from < text.length) parts.push(text.slice(from))
  return <>{parts}</>
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

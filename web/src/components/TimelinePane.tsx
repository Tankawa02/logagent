import { useQuery } from '@tanstack/react-query'
import { MessageSquarePlus, X } from 'lucide-react'
import { useState } from 'react'
import { api, scopeKey, type Scope } from '../lib/api'
import { baseName, formatRange, stampParts } from '../lib/format'
import type { Bucket, SourceTarget, Timeline } from '../lib/types'
import { TimelineChart } from './Timeline'
import { Badge, Button, Empty, ErrorBox, Spinner } from './ui'

const BUCKET_CHOICES = [60, 120, 240]

export function spikeQuestion(bucket: Bucket, data: Timeline): string {
  const range = formatRange(bucket.start, bucket.end, data.time_only)
  const top = bucket.top[0]
  const where = top ? `，主要是「${top.signature}」（${top.count} 次，首次出现在 ${baseName(top.source)}:${top.line}）` : ''
  const others = bucket.top.length > 1 ? `，另有 ${bucket.top.slice(1).map((t) => `「${t.signature}」×${t.count}`).join('、')}` : ''
  return (
    `请分析 ${range}（${data.timezone}）这段时间的错误尖峰：共 ${bucket.error} 条 ERROR/FATAL${where}${others}。` +
    '这波错误的直接原因和根因是什么？与之前的结论是否一致？'
  )
}

export function TimelinePane({
  scope,
  canAsk,
  onOpen,
  onAsk,
}: {
  scope: Scope
  canAsk: boolean
  onOpen: (target: SourceTarget) => void
  onAsk: (question: string) => void
}) {
  const [buckets, setBuckets] = useState(120)
  const [selected, setSelected] = useState<number | null>(null)
  const timeline = useQuery({
    queryKey: [...scopeKey(scope), 'timeline', buckets],
    queryFn: () => api.timeline(scope, buckets),
    staleTime: 60_000,
  })
  const data = timeline.data
  const bucket = selected !== null ? data?.buckets[selected] : undefined

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-100 px-4 py-2.5 dark:border-zinc-800">
        {data && (
          <span className="text-xs text-zinc-500">
            共 {data.totals.events.toLocaleString()} 条 · <span className="text-red-600 dark:text-red-400">ERROR {data.totals.error}</span> · WARN{' '}
            {data.totals.warn} · {data.timezone}
          </span>
        )}
        {timeline.isFetching && <Spinner />}
        <label className="ml-auto flex items-center gap-1.5 text-xs text-zinc-500">
          精度
          <select
            value={buckets}
            onChange={(e) => {
              setBuckets(Number(e.target.value))
              setSelected(null)
            }}
            className="rounded-md border border-zinc-300 bg-white px-1.5 py-0.5 text-xs dark:border-zinc-700 dark:bg-zinc-900"
          >
            {BUCKET_CHOICES.map((n) => (
              <option key={n} value={n}>
                约 {n} 格
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {timeline.error && (
          <div className="p-4">
            <ErrorBox error={timeline.error} />
          </div>
        )}
        {timeline.isLoading && <Empty>正在扫描日志…（大文件首次扫描需要一点时间，之后会缓存）</Empty>}
        {data && (
          <>
            {!!data.spikes.length && (
              <div className="flex flex-wrap items-center gap-1 px-4 pt-3">
                <span className="text-xs text-zinc-500">错误尖峰：</span>
                {data.spikes.slice(0, 8).map((i) => {
                  const b = data.buckets[i]
                  return (
                    <button
                      key={i}
                      type="button"
                      onClick={() => setSelected(i)}
                      className={`rounded-md px-1.5 py-0.5 text-xs ring-1 ring-inset ${
                        selected === i
                          ? 'bg-red-600 text-white ring-red-600'
                          : 'bg-red-50 text-red-700 ring-red-200 hover:bg-red-100 dark:bg-red-950/50 dark:text-red-300 dark:ring-red-900'
                      }`}
                    >
                      {stampParts(b.start).time} · {b.error}
                    </button>
                  )
                })}
              </div>
            )}
            <TimelineChart data={data} selected={selected} onSelect={setSelected} />
            {data.files.some((f) => f.error) && (
              <div className="space-y-2 px-4 pb-3">
                {data.files
                  .filter((f) => f.error)
                  .map((f) => (
                    <ErrorBox key={f.source} error={`${f.name}：${f.error}`} />
                  ))}
              </div>
            )}
            {bucket && (
              <BucketDetail
                bucket={bucket}
                data={data}
                canAsk={canAsk}
                onOpen={onOpen}
                onAsk={() => onAsk(spikeQuestion(bucket, data))}
                onClose={() => setSelected(null)}
              />
            )}
            {!bucket && <p className="px-4 py-3 text-xs text-zinc-500">点击柱子查看该时段的错误分布，并可直接跳到原文或针对该时段追问。</p>}
          </>
        )}
      </div>
    </div>
  )
}

function BucketDetail({
  bucket,
  data,
  canAsk,
  onOpen,
  onAsk,
  onClose,
}: {
  bucket: Bucket
  data: Timeline
  canAsk: boolean
  onOpen: (target: SourceTarget) => void
  onAsk: () => void
  onClose: () => void
}) {
  const firstError = bucket.first_error[0]
  const first = bucket.first[0]
  return (
    <div className="space-y-3 border-t border-zinc-100 px-4 py-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{formatRange(bucket.start, bucket.end, data.time_only)}</span>
        {bucket.spike && <Badge tone="red">错误尖峰</Badge>}
        <span className="text-xs text-zinc-500">
          ERROR {bucket.error} · WARN {bucket.warn} · 共 {bucket.total}
        </span>
        <Button variant="ghost" className="ml-auto" onClick={onClose} aria-label="关闭时段详情">
          <X className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        {firstError ? (
          <Button onClick={() => onOpen({ source: firstError.source, start: firstError.line, end: firstError.line })}>跳到第一条错误</Button>
        ) : (
          first && <Button onClick={() => onOpen({ source: first.source, start: first.line, end: first.line })}>跳到该时段日志</Button>
        )}
        {canAsk && bucket.error > 0 && (
          <Button variant="primary" onClick={onAsk}>
            <MessageSquarePlus className="h-4 w-4" aria-hidden />
            追问这个时段
          </Button>
        )}
      </div>
      {bucket.top.length > 0 && (
        <ul className="space-y-1">
          {bucket.top.map((t) => (
            <li key={t.signature} className="flex items-center gap-2 text-xs">
              <span className="w-10 shrink-0 text-right font-semibold tabular-nums text-red-600 dark:text-red-400">×{t.count}</span>
              <button
                type="button"
                onClick={() => onOpen({ source: t.source, start: t.line, end: t.line })}
                className="min-w-0 flex-1 truncate text-left font-mono text-zinc-700 hover:text-brand-700 hover:underline dark:text-zinc-300"
                title="查看首次出现的原文"
              >
                {t.signature}
              </button>
              <span className="shrink-0 font-mono text-zinc-400">
                {baseName(t.source)}:{t.line}
              </span>
            </li>
          ))}
        </ul>
      )}
      {bucket.error === 0 && <p className="text-xs text-zinc-500">该时段没有 ERROR / FATAL 日志。</p>}
    </div>
  )
}

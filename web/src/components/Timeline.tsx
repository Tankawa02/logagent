import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { formatRange, formatWidth, stampParts } from '../lib/format'
import type { Bucket, Timeline as TimelineData } from '../lib/types'

const HEIGHT = 150
const AXIS = 22
const TOP = 10

type Mode = 'all' | 'errors'

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)))
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

function tickLabel(bucket: Bucket, spanDays: boolean, timeOnly: boolean, seconds: number) {
  const { date, time } = stampParts(bucket.start)
  const clock = seconds >= 60 ? time.slice(0, 5) : time
  return spanDays && !timeOnly ? `${date.slice(5)} ${clock}` : clock
}

export function TimelineChart({
  data,
  selected,
  onSelect,
}: {
  data: TimelineData
  selected: number | null
  onSelect: (index: number) => void
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [mode, setMode] = useState<Mode>('all')
  const [hover, setHover] = useState<number | null>(null)
  const buckets = data.buckets
  const n = buckets.length

  const max = useMemo(() => {
    const values = buckets.map((b) => (mode === 'errors' ? b.error : b.total))
    return Math.max(1, ...values, mode === 'errors' ? data.threshold : 0)
  }, [buckets, mode, data.threshold])

  const spanDays = useMemo(() => {
    if (!n) return false
    return stampParts(buckets[0].start).date !== stampParts(buckets[n - 1].start).date
  }, [buckets, n])

  const ticks = useMemo(() => {
    if (!n || !width) return []
    const count = Math.max(2, Math.min(8, Math.floor(width / 110)))
    const step = Math.max(1, Math.round((n - 1) / (count - 1)))
    const result: number[] = []
    for (let i = 0; i < n; i += step) result.push(i)
    return result
  }, [n, width])

  if (!n) {
    return (
      <p className="px-4 py-8 text-center text-sm text-gray-500">
        日志里没有识别到时间戳，无法绘制时间线。可以在 .log-agent.toml 里用 [[log_formats]] 定义格式。
      </p>
    )
  }

  const plot = HEIGHT - AXIS - TOP
  const barWidth = width / n
  const gap = barWidth > 4 ? 1 : 0
  const y = (value: number) => (value / max) * plot
  const active = hover ?? selected

  function onKey(event: KeyboardEvent) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const current = selected ?? (event.key === 'ArrowLeft' ? n : -1)
    onSelect(Math.max(0, Math.min(n - 1, current + (event.key === 'ArrowLeft' ? -1 : 1))))
  }

  return (
    <div className="px-4 pb-3 pt-2">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
        <div className="flex flex-wrap items-center gap-3">
          <Legend color="bg-red-500" label={`ERROR ${data.totals.error.toLocaleString()}`} />
          <Legend color="bg-amber-400" label={`WARN ${data.totals.warn.toLocaleString()}`} />
          {mode === 'all' && <Legend color="bg-gray-300 dark:bg-gray-600" label={`全部 ${data.totals.events.toLocaleString()}`} />}
          <span>
            每格 {formatWidth(data.bucket_seconds)} · 时区 {data.timezone}
          </span>
        </div>
        <div className="inline-flex rounded-md border border-gray-200 p-0.5 dark:border-gray-700">
          {(['all', 'errors'] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              className={`rounded px-2 py-0.5 ${mode === value ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900' : 'hover:bg-gray-100 dark:hover:bg-gray-800'}`}
            >
              {value === 'all' ? '全部日志' : '只看错误'}
            </button>
          ))}
        </div>
      </div>

      <div ref={ref} className="relative w-full select-none" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            aria-label="错误时间线，点击柱子查看该时段详情"
            tabIndex={0}
            onKeyDown={onKey}
            className="block outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          >
            {[0.5, 1].map((f) => (
              <line
                key={f}
                x1={0}
                x2={width}
                y1={TOP + plot - plot * f}
                y2={TOP + plot - plot * f}
                className="stroke-gray-100 dark:stroke-gray-800"
              />
            ))}
            {buckets.map((b, i) => {
              const x = i * barWidth
              const errorH = y(b.error)
              const warnH = mode === 'all' ? y(b.warn) : 0
              const otherH = mode === 'all' ? y(Math.max(0, b.total - b.error - b.warn)) : 0
              const base = TOP + plot
              return (
                <g key={i} onMouseEnter={() => setHover(i)} onClick={() => onSelect(i)} className="cursor-pointer">
                  <rect
                    x={x}
                    y={TOP}
                    width={Math.max(barWidth, 1)}
                    height={plot}
                    className={
                      i === selected
                        ? 'fill-sky-100 dark:fill-sky-950'
                        : b.spike
                          ? 'fill-red-50 dark:fill-red-950/40'
                          : i === hover
                            ? 'fill-gray-100 dark:fill-gray-800'
                            : 'fill-transparent'
                    }
                  />
                  <rect x={x + gap} y={base - otherH - warnH - errorH} width={Math.max(barWidth - 2 * gap, 0.5)} height={otherH} className="fill-gray-300 dark:fill-gray-600" />
                  <rect x={x + gap} y={base - warnH - errorH} width={Math.max(barWidth - 2 * gap, 0.5)} height={warnH} className="fill-amber-400" />
                  <rect x={x + gap} y={base - errorH} width={Math.max(barWidth - 2 * gap, 0.5)} height={errorH} className="fill-red-500" />
                  {b.spike && (
                    <path
                      d={`M ${x + barWidth / 2 - 4} ${TOP - 1} l 4 6 l 4 -6 z`}
                      className="fill-red-600 dark:fill-red-400"
                    />
                  )}
                </g>
              )
            })}
            {mode === 'errors' && data.threshold > 0 && (
              <g>
                <line
                  x1={0}
                  x2={width}
                  y1={TOP + plot - y(data.threshold)}
                  y2={TOP + plot - y(data.threshold)}
                  strokeDasharray="4 3"
                  className="stroke-red-300 dark:stroke-red-800"
                />
                <text x={width - 4} y={TOP + plot - y(data.threshold) - 3} textAnchor="end" className="fill-red-400 text-[10px]">
                  尖峰阈值 {Math.ceil(data.threshold)}
                </text>
              </g>
            )}
            <line x1={0} x2={width} y1={TOP + plot} y2={TOP + plot} className="stroke-gray-300 dark:stroke-gray-700" />
            {ticks.map((i) => (
              <text
                key={i}
                x={Math.min(Math.max(i * barWidth + barWidth / 2, 24), width - 24)}
                y={HEIGHT - 6}
                textAnchor="middle"
                className="fill-gray-500 text-[10px] dark:fill-gray-400"
              >
                {tickLabel(buckets[i], spanDays, data.time_only, data.bucket_seconds)}
              </text>
            ))}
          </svg>
        )}
        {active !== null && buckets[active] && width > 0 && (
          <div
            className="pointer-events-none absolute top-0 z-10 rounded-md border border-gray-200 bg-white/95 px-2 py-1 text-xs shadow-md dark:border-gray-700 dark:bg-gray-900/95"
            style={{
              left: Math.min(Math.max(active * barWidth + barWidth / 2 - 90, 0), Math.max(width - 180, 0)),
              width: 180,
            }}
          >
            <div className="font-medium text-gray-800 dark:text-gray-100">
              {formatRange(buckets[active].start, buckets[active].end, data.time_only)}
            </div>
            <div className="text-gray-600 dark:text-gray-300">
              <span className="text-red-600 dark:text-red-400">ERROR {buckets[active].error}</span> · WARN{' '}
              {buckets[active].warn} · 共 {buckets[active].total}
            </div>
            {buckets[active].spike && <div className="text-red-600 dark:text-red-400">错误尖峰 · 点击查看并追问</div>}
          </div>
        )}
      </div>
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`h-2.5 w-2.5 rounded-sm ${color}`} />
      {label}
    </span>
  )
}

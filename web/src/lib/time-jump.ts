import { createContext, useContext } from 'react'
import type { Bucket } from './types'

/** 回答里点了某个时间点：交给工作区打开时间线并定位到对应时段 */
export const TimeJumpContext = createContext<((at: string) => void) | null>(null)

export function useTimeJump() {
  return useContext(TimeJumpContext)
}

export const TIME_HREF = '#log-agent-time='

/** `2024-05-01 10:05:03`、`10:05`、`10:05:03.120` 这类时间点；前后不能紧挨着数字，避免误伤端口 / 版本号 */
const TIME_POINT = /(?<![\d:.])(?:(\d{4}-\d{2}-\d{2})[ T])?(\d{1,2}):(\d{2})(?::(\d{2})(?:[.,]\d{1,6})?)?(?![\d:])/g

export interface TimeMatch {
  index: number
  text: string
  value: string
}

export function findTimes(text: string): TimeMatch[] {
  const out: TimeMatch[] = []
  for (const m of text.matchAll(TIME_POINT)) {
    const [, date, h, min, sec] = m
    if (Number(h) > 23 || Number(min) > 59 || (sec && Number(sec) > 59)) continue
    const time = `${h.padStart(2, '0')}:${min}:${sec ?? '00'}`
    out.push({ index: m.index ?? 0, text: m[0], value: date ? `${date}T${time}` : time })
  }
  return out
}

/** 找出时间点所在的时间线格子；带日期时按完整时间比较，只有时分秒时按当天时刻比较 */
export function bucketAt(buckets: Bucket[], at: string): number | null {
  const withDate = at.includes('T')
  const key = (iso: string) => (withDate ? iso.slice(0, 19) : (iso.split('T')[1] ?? '').slice(0, 8))
  for (let i = 0; i < buckets.length; i++) {
    const b = buckets[i]
    const start = key(b.start)
    const end = key(b.end)
    if (at >= start && (at < end || (i === buckets.length - 1 && at <= end))) return i
  }
  return null
}

type MdNode = { type: string; value?: string; url?: string; children?: MdNode[] }

const SKIP = new Set(['link', 'linkReference', 'inlineCode', 'code', 'html'])

/** remark 插件：把正文里的时间点变成特殊链接，由 Markdown 组件渲染成可点击的跳转 */
export function remarkTimePoints() {
  return (tree: MdNode) => walk(tree)
}

function walk(node: MdNode) {
  if (!node.children || SKIP.has(node.type)) return
  const next: MdNode[] = []
  for (const child of node.children) {
    if (child.type !== 'text' || !child.value) {
      walk(child)
      next.push(child)
      continue
    }
    const value = child.value
    let from = 0
    for (const m of findTimes(value)) {
      if (m.index > from) next.push({ type: 'text', value: value.slice(from, m.index) })
      next.push({ type: 'link', url: TIME_HREF + m.value, children: [{ type: 'text', value: m.text }] })
      from = m.index + m.text.length
    }
    if (from === 0) next.push(child)
    else if (from < value.length) next.push({ type: 'text', value: value.slice(from) })
  }
  node.children = next
}

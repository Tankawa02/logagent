import { describe, expect, it } from 'vitest'
import { recentSources } from '../pages/NewSession'
import { peakBucket } from '../components/TimelinePane'
import { relativeTime } from './format'
import { bucketAt, findTimes } from './time-jump'
import type { Bucket, SessionSummary, Timeline } from './types'

describe('findTimes', () => {
  it('finds clock times and full timestamps', () => {
    expect(findTimes('10:05 开始报错，2026-10-08 14:00:03.120 恢复').map((m) => m.value)).toEqual(['10:05:00', '2026-10-08T14:00:03'])
  })

  it('ignores ports, ratios and invalid clock values', () => {
    expect(findTimes('mongos4/5/6:20001 连接失败')).toEqual([])
    expect(findTimes('版本 1.10:30 / 比例 25:61 / 时刻 24:00')).toEqual([])
  })
})

function bucket(i: number, start: string, end: string, error: number): Bucket {
  return { index: i, start, end, start_ts: i, total: error, warn: 0, error, first: [], first_error: [], top: [] }
}

const buckets = [
  bucket(0, '2026-10-08T10:00:00+08:00', '2026-10-08T10:05:00+08:00', 1),
  bucket(1, '2026-10-08T10:05:00+08:00', '2026-10-08T10:10:00+08:00', 9),
  bucket(2, '2026-10-08T10:10:00+08:00', '2026-10-08T10:15:00+08:00', 3),
]

describe('bucketAt', () => {
  it('locates times with or without a date', () => {
    expect(bucketAt(buckets, '10:07:30')).toBe(1)
    expect(bucketAt(buckets, '2026-10-08T10:10:00')).toBe(2)
    expect(bucketAt(buckets, '10:15:00')).toBe(2)
    expect(bucketAt(buckets, '11:00:00')).toBeNull()
  })
})

describe('peakBucket', () => {
  it('picks the bucket with the most errors, or none when clean', () => {
    expect(peakBucket({ buckets } as Timeline)).toBe(1)
    expect(peakBucket({ buckets: buckets.map((b) => ({ ...b, error: 0 })) } as Timeline)).toBeNull()
  })
})

describe('recentSources', () => {
  it('dedupes paths from the most recently updated sessions and skips missing files', () => {
    const session = (updated_at: string, logs: string[], code: string[] = [], missing = false) =>
      ({
        updated_at,
        logs: logs.map((path) => ({ path, name: path, exists: !missing })),
        code: code.map((path) => ({ path, name: path })),
      }) as unknown as SessionSummary
    const recent = recentSources([
      session('2026-10-01T00:00:00Z', ['/a.log'], ['/repo']),
      session('2026-10-05T00:00:00Z', ['/b.log', '/a.log']),
      session('2026-10-09T00:00:00Z', ['/gone.log'], [], true),
    ])
    expect(recent).toEqual({ logs: ['/b.log', '/a.log'], code: ['/repo'] })
  })
})

describe('relativeTime', () => {
  const now = Date.parse('2026-10-10T12:00:00Z')
  it('formats recent updates relative to now', () => {
    expect(relativeTime('2026-10-10T11:59:40Z', now)).toBe('刚刚')
    expect(relativeTime('2026-10-10T11:15:00Z', now)).toBe('45 分钟前')
    expect(relativeTime('2026-10-10T09:00:00Z', now)).toBe('3 小时前')
    expect(relativeTime('2026-10-08T12:00:00Z', now)).toBe('2 天前')
  })
})

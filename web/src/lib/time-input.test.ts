import { describe, expect, it } from 'vitest'
import { checkBound, checkRange, checkTimezone } from './time-input'

describe('checkBound', () => {
  it('accepts the same shapes as the server parser', () => {
    expect(checkBound('2026-10-08 14:00', false)).toMatchObject({ ok: true, stamp: '2026-10-08 14:00:00' })
    expect(checkBound('2026/10/08T14:00:30', false)).toMatchObject({ ok: true, stamp: '2026-10-08 14:00:30' })
    expect(checkBound('2026-10-08 14:00+08:00', false)).toMatchObject({ ok: true, order: null })
    expect(checkBound('14:00', false)).toMatchObject({ ok: true, stamp: '14:00:00', order: null })
  })

  it('widens upper bounds to the given precision', () => {
    expect(checkBound('2026-10-08', true)).toMatchObject({ ok: true, stamp: '2026-10-08 23:59:59' })
    expect(checkBound('14:05', true)).toMatchObject({ ok: true, stamp: '14:05:59' })
  })

  it('rejects what the server would reject', () => {
    expect(checkBound('2h', false)).toMatchObject({ ok: false })
    expect(checkBound('2026-02-30', false)).toMatchObject({ ok: false })
    expect(checkBound('25:00', false)).toMatchObject({ ok: false })
    expect(checkBound('14:00+08:00', false)).toMatchObject({ ok: false })
    expect(checkBound('  ', false)).toBeNull()
  })
})

describe('checkRange', () => {
  it('splits on ~ or a single dash', () => {
    expect(checkRange('13:00~13:30')).toMatchObject({ ok: true, stamp: '13:00:00 ~ 13:30:59' })
    expect(checkRange('13:00-13:30')).toMatchObject({ ok: true })
    expect(checkRange('2026-10-07 14:00~2026-10-07 15:00')).toMatchObject({ ok: true })
  })

  it('reports malformed or reversed ranges', () => {
    expect(checkRange('2026-10-07 14:00..2026-10-07 15:00')).toMatchObject({ ok: false })
    expect(checkRange('13:00~')).toMatchObject({ ok: false })
    expect(checkRange('2026-10-07 15:00~2026-10-07 14:00')).toMatchObject({ ok: false, error: '起点晚于终点' })
  })
})

describe('checkTimezone', () => {
  it('accepts UTC, offsets and IANA names', () => {
    expect(checkTimezone('utc')).toMatchObject({ ok: true })
    expect(checkTimezone('+0800')).toMatchObject({ ok: true, text: 'UTC+08:00' })
    expect(checkTimezone('Asia/Shanghai')).toMatchObject({ ok: true })
    expect(checkTimezone('Mars/Base')).toMatchObject({ ok: false })
  })
})

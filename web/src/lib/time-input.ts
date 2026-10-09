/**
 * 新建分析页时间输入的即时校验，规则与服务端 timefilter.parse_bound / compare.parse_range 保持一致：
 * 完整日期（可带时间与时区偏移）或只有时刻；上界按给定精度包含整段。
 */

export type BoundCheck =
  | {
      ok: true
      text: string
      /** 解析出的时间本身，不带「从 / 到」 */
      stamp: string
      /** 可比较的毫秒数；只有时刻或带时区时为 null，不参与先后校验 */
      order: number | null
    }
  | { ok: false; error: string }

export const BOUND_EXAMPLES = '2026-10-08 14:00、2026-10-08、14:00'

const DATE_RE = /^(\d{4})-(\d{1,2})-(\d{1,2})(?: (\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:\.(\d{1,6}))?)?)?(Z|[+-]\d{2}:?\d{2})?$/
const TIME_RE = /^(\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:\.(\d{1,6}))?)?$/

const pad = (n: number) => String(n).padStart(2, '0')

function clockText(h: number, m: number, s: number) {
  return `${pad(h)}:${pad(m)}:${pad(s)}`
}

function invalid(raw: string): BoundCheck {
  return { ok: false, error: `无法识别「${raw}」，可以写成 ${BOUND_EXAMPLES}` }
}

export function checkBound(input: string, upper: boolean): BoundCheck | null {
  const raw = input.trim()
  if (!raw) return null
  const normalized = raw.replaceAll('/', '-').replace('T', ' ')

  const date = DATE_RE.exec(normalized)
  if (date) {
    const [, y, mo, d, h, mi, s, , zone] = date
    const year = Number(y)
    const month = Number(mo)
    const day = Number(d)
    const probe = new Date(Date.UTC(year, month - 1, day))
    if (probe.getUTCMonth() !== month - 1 || probe.getUTCDate() !== day) return invalid(raw)
    const hasTime = h !== undefined
    const hour = hasTime ? Number(h) : upper ? 23 : 0
    const minute = hasTime ? Number(mi) : upper ? 59 : 0
    const second = s !== undefined ? Number(s) : upper ? 59 : 0
    if (hour > 23 || minute > 59 || second > 59) return invalid(raw)
    const day10 = `${year}-${pad(month)}-${pad(day)}`
    const stamp = `${day10} ${clockText(hour, minute, second)}${zone ? ` ${zone}` : ''}`
    return {
      ok: true,
      text: upper ? `到 ${stamp} 为止（含）` : `从 ${stamp} 起`,
      stamp,
      order: zone ? null : Date.UTC(year, month - 1, day, hour, minute, second),
    }
  }

  if (/(Z|[+-]\d{2}:?\d{2})$/.test(normalized) && !TIME_RE.test(normalized)) {
    return { ok: false, error: `带时区的时间需要写完整日期，如 2026-10-08 14:00+08:00` }
  }

  const clock = TIME_RE.exec(normalized)
  if (clock) {
    const [, h, mi, s] = clock
    const hour = Number(h)
    const minute = Number(mi)
    const second = s !== undefined ? Number(s) : upper ? 59 : 0
    if (hour > 23 || minute > 59 || second > 59) return invalid(raw)
    const text = clockText(hour, minute, second)
    return {
      ok: true,
      text: upper ? `每天到 ${text} 为止（只比较时刻）` : `每天 ${text} 起（只比较时刻）`,
      stamp: text,
      order: null,
    }
  }

  return invalid(raw)
}

export function checkRange(input: string): BoundCheck | null {
  const raw = input.trim()
  if (!raw) return null
  let since: string
  let until: string
  if (raw.includes('~')) {
    ;[since, until] = [raw.slice(0, raw.indexOf('~')), raw.slice(raw.indexOf('~') + 1)]
  } else if (raw.split('-').length === 2) {
    ;[since, until] = raw.split('-')
  } else {
    return { ok: false, error: '用 ~ 分隔起止时间，如 13:00~13:30 或 2026-10-07 14:00~2026-10-07 15:00' }
  }
  since = since.trim()
  until = until.trim()
  if (!since || !until) return { ok: false, error: '时间段需要同时写出起点和终点' }
  const start = checkBound(since, false)
  const end = checkBound(until, true)
  if (!start || !start.ok) return start
  if (!end || !end.ok) return end
  if (start.order !== null && end.order !== null && start.order > end.order) {
    return { ok: false, error: '起点晚于终点' }
  }
  const stamp = `${start.stamp} ~ ${end.stamp}`
  return { ok: true, text: `对比时段 ${stamp}`, stamp, order: null }
}

export function checkTimezone(input: string): BoundCheck | null {
  const raw = input.trim()
  if (!raw) return null
  if (/^(utc|z)$/i.test(raw)) return { ok: true, text: 'UTC', stamp: 'UTC', order: null }
  const offset = /^([+-])(\d{2}):?(\d{2})$/.exec(raw)
  if (offset) {
    if (Number(offset[2]) < 24 && Number(offset[3]) < 60) {
      const text = `UTC${offset[1]}${offset[2]}:${offset[3]}`
      return { ok: true, text, stamp: text, order: null }
    }
  } else {
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: raw })
      return { ok: true, text: raw, stamp: raw, order: null }
    } catch {
      // 落到下面的统一报错
    }
  }
  return { ok: false, error: `无法识别的时区「${raw}」，可以写 UTC、+08:00 或 Asia/Shanghai` }
}

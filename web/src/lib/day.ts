/** "2026-10-14 10:27:03" → 今天 / 昨天 / 10月14日 / 2025年10月14日 */
export function formatDay(value: string): string {
  const date = new Date(value.replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return value.slice(0, 10)
  const today = new Date()
  const days = Math.round((new Date(today.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000)
  if (days === 0) return '今天'
  if (days === 1) return '昨天'
  const md = `${date.getMonth() + 1}月${date.getDate()}日`
  return date.getFullYear() === today.getFullYear() ? md : `${date.getFullYear()}年${md}`
}

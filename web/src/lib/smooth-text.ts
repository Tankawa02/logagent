import { useEffect, useState } from 'react'

/** 每帧至少放出这么多字，积压时按比例加速，积压再多也能在约半秒内追上 */
const MIN_STEP = 2
const CATCH_UP_DIVISOR = 6

/** 下一帧要显示到第几个字符：不会切开代理对（emoji 等），避免闪一下乱码 */
export function nextLength(text: string, from: number): number {
  if (from >= text.length) return text.length
  let next = Math.min(text.length, from + Math.max(MIN_STEP, Math.ceil((text.length - from) / CATCH_UP_DIVISOR)))
  const last = text.charCodeAt(next - 1)
  if (last >= 0xd800 && last <= 0xdbff && next < text.length) next++
  return next
}

/**
 * 模型的增量是一阵一阵到的（网络和推理都会攒批），直接显示就是「一顿一顿」地蹦字。
 * 流式进行中把收到的文本按帧匀速放出来；结束（active 变为 false）后立刻显示全文。
 */
export function useSmoothText(text: string, active: boolean): string {
  const [length, setLength] = useState(() => (active ? 0 : text.length))
  useEffect(() => {
    if (!active || length >= text.length) return
    const frame = requestAnimationFrame(() => setLength(nextLength(text, length)))
    return () => cancelAnimationFrame(frame)
  }, [active, text, length])
  return active ? text.slice(0, Math.min(length, text.length)) : text
}

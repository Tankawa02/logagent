import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** 离底部这么近就算「在看最新」，流式输出时继续跟随 */
const STICK_THRESHOLD = 80

/**
 * 只有用户本来就停在底部附近时才跟随新内容滚动；往上翻看时不打扰，
 * 由调用方根据 `atBottom` 显示「回到最新」。
 */
export function useStickToBottom(ref: RefObject<HTMLElement | null>, deps: unknown[]) {
  const pinned = useRef(true)
  const [atBottom, setAtBottom] = useState(true)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onScroll = () => {
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD
      pinned.current = near
      setAtBottom(near)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [ref])

  useLayoutEffect(() => {
    const el = ref.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps

  const scrollToBottom = useCallback(
    (behavior: ScrollBehavior = 'smooth') => {
      const el = ref.current
      if (!el) return
      pinned.current = true
      setAtBottom(true)
      el.scrollTo({ top: el.scrollHeight, behavior })
    },
    [ref],
  )

  return { atBottom, scrollToBottom }
}

/**
 * 模态框的键盘行为：打开时把焦点移进来，Tab 在框内循环，Esc 关闭，关闭后焦点回到原来的位置。
 * 返回的 onKeyDown 挂在对话框根元素上。
 */
export function useModal(ref: RefObject<HTMLElement | null>, onClose: () => void, { trap = true, enabled = true } = {}) {
  useEffect(() => {
    if (!enabled) return
    const previous = document.activeElement as HTMLElement | null
    const el = ref.current
    const first = el?.querySelector<HTMLElement>('[data-autofocus]') ?? el?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? el)?.focus()
    return () => {
      if (previous && document.contains(previous)) previous.focus()
    }
  }, [ref, enabled])

  return useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (!trap || event.key !== 'Tab' || !ref.current) return
      const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null)
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    },
    [ref, onClose, trap],
  )
}

/** 点击元素外部时回调（菜单、浮层关闭用） */
export function useClickOutside(ref: RefObject<HTMLElement | null>, onOutside: () => void, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return
    const handler = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onOutside()
    }
    document.addEventListener('pointerdown', handler)
    return () => document.removeEventListener('pointerdown', handler)
  }, [ref, onOutside, enabled])
}

export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)
  useEffect(() => {
    const list = window.matchMedia(query)
    const onChange = () => setMatches(list.matches)
    onChange()
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])
  return matches
}

/** 复制到剪贴板，成功或失败的提示 1.5 秒后自动消失，卸载时清理计时器 */
export function useCopy() {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  const copy = useCallback(async (text: string) => {
    clearTimeout(timer.current)
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
    timer.current = setTimeout(() => setState('idle'), 1500)
  }, [])
  return { state, copy }
}

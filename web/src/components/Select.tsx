import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'

export type SelectOption = { value: string; label: string; hint?: string; title?: string }

/**
 * 与 ModelCombobox 同一套视觉的单选下拉，替代原生 <select>（原生下拉在深色模式和各浏览器里样式不一致）。
 * 触发器是 button，可以被 <label htmlFor> 关联；键盘：↑↓ 移动、Enter/空格 选中、Esc 关闭、首字母跳转。
 */
export function Select({
  id,
  value,
  onChange,
  options,
  ariaLabel,
  className = '',
  size = 'md',
  align = 'left',
}: {
  id?: string
  value: string
  onChange: (value: string) => void
  options: SelectOption[]
  ariaLabel?: string
  className?: string
  /** sm：工具栏里的紧凑下拉；md：与记忆、筛选输入框同高；lg：与设置、skill 表单输入框同高 */
  size?: 'sm' | 'md' | 'lg'
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [dropUp, setDropUp] = useState(false)
  const listId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const selectedIndex = options.findIndex((o) => o.value === value)
  const current = options[selectedIndex]

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  const show = () => {
    const rect = rootRef.current?.getBoundingClientRect()
    // 下方放不下（例如靠近底部固定的保存栏）且上方更宽裕时向上展开
    if (rect) setDropUp(window.innerHeight - rect.bottom < 300 && rect.top > window.innerHeight - rect.bottom)
    setActive(Math.max(selectedIndex, 0))
    setOpen(true)
    requestAnimationFrame(() => listRef.current?.children[Math.max(selectedIndex, 0)]?.scrollIntoView({ block: 'nearest' }))
  }

  const move = (next: number) => {
    setActive(next)
    listRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
  }

  const choose = (next: string) => {
    if (next !== value) onChange(next)
    setOpen(false)
    buttonRef.current?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
        e.preventDefault()
        show()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      move(Math.min(active + 1, options.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      move(Math.max(active - 1, 0))
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault()
      move(e.key === 'Home' ? 0 : options.length - 1)
    } else if ((e.key === 'Enter' || e.key === ' ') && options[active]) {
      e.preventDefault()
      choose(options[active].value)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    } else if (e.key === 'Tab') {
      setOpen(false)
    } else if (e.key.length === 1) {
      const key = e.key.toLowerCase()
      const start = active + 1
      const hit = [...options.slice(start), ...options.slice(0, start)].find((o) => o.label.toLowerCase().startsWith(key))
      if (hit) move(options.indexOf(hit))
    }
  }

  const sizing = {
    sm: 'rounded-md py-0.5 pl-2 pr-6 text-xs',
    md: 'rounded-md py-1.5 pl-2.5 pr-8 text-sm',
    lg: 'rounded-lg py-2 pl-3 pr-8 text-sm dark:border-zinc-700',
  }[size]

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        ref={buttonRef}
        id={id}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onKeyDown}
        title={current?.title ?? current?.label}
        className={`flex w-full items-center border border-zinc-200 bg-white text-left text-zinc-800 outline-none transition-colors hover:border-zinc-300 focus-visible:border-brand-500 focus-visible:ring-2 focus-visible:ring-brand-500/20 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 ${size === 'lg' ? '' : 'dark:border-zinc-800'} ${sizing}`}
      >
        <span className="truncate">{current?.label ?? '请选择'}</span>
        <ChevronDown
          className={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-zinc-400 transition-transform ${
            size === 'sm' ? 'right-1.5 h-3 w-3' : 'right-2.5 h-4 w-4'
          } ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
      </button>
      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={ariaLabel}
          className={`absolute z-40 ${dropUp ? 'bottom-full mb-1.5' : 'top-full mt-1.5'} max-h-64 min-w-full animate-menu-in overflow-auto rounded-xl motion-reduce:animate-none border border-zinc-200 bg-white p-1 shadow-lg dark:border-zinc-800 dark:bg-zinc-900 ${
            align === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          {options.map((o, i) => {
            const selected = o.value === value
            return (
              <li
                key={o.value}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={selected}
                title={o.title}
                // mousedown 先于 button 失焦，避免点击前列表被关掉
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(o.value)
                }}
                onMouseEnter={() => setActive(i)}
                className={`flex cursor-pointer items-center justify-between gap-3 whitespace-nowrap rounded-lg px-2.5 py-1.5 ${
                  size === 'sm' ? 'text-xs' : 'text-[13px]'
                } ${i === active ? 'bg-zinc-100 dark:bg-zinc-800' : ''} ${
                  selected ? 'text-brand-700 dark:text-brand-300' : 'text-zinc-700 dark:text-zinc-200'
                }`}
              >
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="truncate">{o.label}</span>
                  {o.hint && <span className="truncate text-xs text-zinc-400 dark:text-zinc-500">{o.hint}</span>}
                </span>
                {selected && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

import { Check, ChevronDown } from 'lucide-react'
import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'

/** 和服务端一致：不带 provider 前缀的按 openai 处理 */
function normalizeModel(value: string) {
  const v = value.trim()
  return v.includes(':') ? v : `openai:${v}`
}

/**
 * 填写的模型不在列表里时返回 true。
 * 列表为空（接口不支持 /models、还没配 Key 等）时无从判断，按用户填写的放行。
 */
export function isUnknownModel(value: string, options: string[]) {
  if (!value.trim() || options.length === 0) return false
  return !options.includes(normalizeModel(value))
}

export function ModelCombobox({
  id,
  value,
  onChange,
  options,
  defaultModel,
  loading,
  validate = true,
  className,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  options: string[]
  defaultModel?: string
  loading?: boolean
  /** 列表可能已过期（比如设置页正在改接口地址）时关掉校验 */
  validate?: boolean
  className: string
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [filtering, setFiltering] = useState(false)
  const listId = useId()
  const errorId = `${listId}-error`
  const unknown = validate && !loading && isUnknownModel(value, options)
  const listRef = useRef<HTMLUListElement>(null)

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase()
    if (!filtering || !q) return options
    return options.filter((m) => m.toLowerCase().includes(q))
  }, [filtering, options, value])

  const choose = (m: string) => {
    onChange(m)
    setOpen(false)
    setFiltering(false)
    setActive(-1)
  }

  const move = (next: number) => {
    setActive(next)
    listRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) setOpen(true)
      if (filtered.length) move(Math.min(active + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (filtered.length) move(Math.max(active - 1, 0))
    } else if (e.key === 'Enter' && open && active >= 0 && filtered[active]) {
      e.preventDefault()
      choose(filtered[active])
    } else if (e.key === 'Enter' && open && filtering && filtered.length === 1 && filtered[0] !== value) {
      // 只剩一个候选时回车直接选中，不用再按方向键
      e.preventDefault()
      choose(filtered[0])
    } else if (e.key === 'Escape' && open) {
      e.preventDefault()
      setOpen(false)
    }
  }

  return (
    <div className="relative">
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        aria-invalid={unknown || undefined}
        aria-describedby={unknown ? errorId : undefined}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          setFiltering(true)
          setOpen(true)
          setActive(-1)
        }}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        placeholder={defaultModel ? `默认 ${defaultModel}` : 'provider:model'}
        autoComplete="off"
        spellCheck={false}
        className={`${className} pr-9 font-mono ${unknown ? 'border-red-400! focus:ring-red-500/10! dark:border-red-700!' : ''}`}
      />
      <ChevronDown
        className={`pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400 transition-transform ${open ? 'rotate-180' : ''}`}
        aria-hidden
      />
      {open && (loading || filtered.length > 0) && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          className="absolute inset-x-0 top-full z-30 mt-1.5 max-h-64 overflow-auto rounded-xl border border-zinc-200 bg-white p-1 shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
        >
          {filtered.map((m, i) => {
            const selected = m === value
            return (
              <li
                key={m}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={selected}
                // mousedown 先于 input blur，避免列表在点击前就被关掉
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(m)
                }}
                onMouseEnter={() => setActive(i)}
                className={`flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 font-mono text-[13px] ${
                  i === active ? 'bg-zinc-100 dark:bg-zinc-800' : ''
                } ${selected ? 'text-brand-700 dark:text-brand-300' : 'text-zinc-700 dark:text-zinc-200'}`}
              >
                <span className="truncate">{m}</span>
                <span className="flex shrink-0 items-center gap-1.5">
                  {m === defaultModel && <span className="font-sans text-xs text-zinc-500">默认</span>}
                  {selected && <Check className="h-3.5 w-3.5" aria-hidden />}
                </span>
              </li>
            )
          })}
          {loading && !filtered.length && <li className="px-2.5 py-1.5 text-xs text-zinc-400">正在读取模型列表…</li>}
        </ul>
      )}
      {unknown && (
        <p id={errorId} className="mt-1 text-xs text-red-600 dark:text-red-400">
          模型列表里没有「{value.trim()}」，请从下拉列表中选择{defaultModel ? '，或清空使用默认模型' : ''}
        </p>
      )}
    </div>
  )
}

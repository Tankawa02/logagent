import { Link } from '@tanstack/react-router'
import { Brain, ChevronDown, Lightbulb } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { MemoryItem, SessionMemory } from '../lib/types'
import { CandidateRow, KIND_TONE } from './MemoryCandidate'
import { Badge } from './ui'

/** 输入框上方的记忆状态：本会话每轮会带上的记忆，点开可查看具体条目 */
export function MemoryStatus({ memory }: { memory: SessionMemory }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const off = memory.mode === 'off'
  const count = memory.memories.length
  const skipped = memory.skipped.length
  const label = off
    ? '记忆已关闭'
    : !memory.available
      ? '记忆库不可用'
      : count
        ? `参考 ${count} 条记忆${skipped ? `（${skipped} 条未带上）` : ''}`
        : '暂无记忆'

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        title="agent 每轮都会带上这些记忆（全局 + 本项目）"
        className={`flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors ${
          count && !off
            ? 'border-sky-200 bg-sky-50 text-sky-700 hover:border-sky-300 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300'
            : 'border-zinc-200 text-zinc-500 hover:border-zinc-300 hover:text-zinc-700 dark:border-zinc-800 dark:text-zinc-400'
        }`}
      >
        <Brain className="h-3.5 w-3.5" aria-hidden />
        {label}
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div
          id={panelId}
          role="region"
          aria-label="本会话使用的记忆"
          className="absolute bottom-full right-0 z-20 mb-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
        >
          <div className="border-b border-zinc-100 px-3 py-2 dark:border-zinc-800">
            <p className="text-xs font-medium text-zinc-800 dark:text-zinc-100">
              {off ? '长期记忆已关闭' : '每轮分析都会带上这些记忆'}
            </p>
            <p className="mt-0.5 text-xs text-zinc-500">
              {off
                ? '在配置文件里把 memory 设为 suggest 或 explicit 后重启服务即可开启。'
                : `范围：全局 + ${memory.project_label}。与本次证据冲突时以证据为准。`}
            </p>
          </div>
          {!off && (
            <ul className="max-h-64 divide-y divide-zinc-100 overflow-auto dark:divide-zinc-800">
              {count === 0 && skipped === 0 && (
                <li className="px-3 py-3 text-xs text-zinc-500">
                  还没有保存的记忆。在对话里说「记住……」，或纠正 agent 的理解，它会提议保存。
                </li>
              )}
              {memory.memories.map((m) => (
                <MemoryLine key={m.id} memory={m} />
              ))}
              {skipped > 0 && (
                <li className="bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-950/40">
                  以下 {skipped} 条超出篇幅上限，本轮没有带上。可以在记忆管理里合并或删除不常用的条目。
                </li>
              )}
              {memory.skipped.map((m) => (
                <MemoryLine key={m.id} memory={m} muted />
              ))}
            </ul>
          )}
          <div className="border-t border-zinc-100 px-3 py-2 text-right dark:border-zinc-800">
            <Link to="/memory" className="text-xs font-medium text-sky-700 hover:underline dark:text-sky-300">
              管理记忆
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}

function MemoryLine({ memory, muted = false }: { memory: MemoryItem; muted?: boolean }) {
  return (
    <li className={`space-y-1 px-3 py-2 ${muted ? 'opacity-60' : ''}`}>
      <div className="flex items-center gap-1.5">
        <Badge tone={muted ? 'gray' : KIND_TONE[memory.kind]}>{memory.kind_label}</Badge>
        <span className="text-xs text-zinc-400">{memory.scope_label}</span>
        {muted && <span className="text-xs text-zinc-400">未带上</span>}
      </div>
      <p className="text-xs leading-relaxed text-zinc-700 dark:text-zinc-200">{memory.text}</p>
    </li>
  )
}

/** 对话里提出的记忆候选：本轮结束后直接在对话流里请用户确认，不必再去 Memory 页面 */
export function MemoryConfirm({ memory }: { memory: SessionMemory }) {
  if (memory.mode === 'off' || memory.pending.length === 0) return null
  return (
    <section
      aria-label="待确认的记忆"
      className="ml-10 overflow-hidden rounded-xl border border-amber-200 bg-amber-50/60 dark:border-amber-900/60 dark:bg-amber-950/20"
    >
      <header className="flex items-center gap-2 border-b border-amber-200/70 px-4 py-2.5 dark:border-amber-900/60">
        <Lightbulb className="h-4 w-4 text-amber-600 dark:text-amber-400" aria-hidden />
        <h3 className="text-sm font-medium text-zinc-900 dark:text-zinc-100">要记住这些吗？</h3>
        <span className="text-xs text-zinc-500">保存后，之后每次分析都会参考</span>
      </header>
      <ul className="divide-y divide-amber-200/70 dark:divide-amber-900/60">
        {memory.pending.map((c) => (
          <CandidateRow key={c.id} candidate={c} />
        ))}
      </ul>
    </section>
  )
}

import { X } from 'lucide-react'
import { useRef } from 'react'
import { useModal } from '../lib/hooks'
import { MOD } from '../lib/shortcuts'
import { Button } from './ui'

const OPEN_EVENT = 'log-agent:shortcuts'

/** 从任意位置（比如侧栏底部的按钮）打开快捷键面板 */
export function openShortcuts() {
  window.dispatchEvent(new Event(OPEN_EVENT))
}

export function onOpenShortcuts(handler: () => void) {
  window.addEventListener(OPEN_EVENT, handler)
  return () => window.removeEventListener(OPEN_EVENT, handler)
}

const GROUPS: { title: string; items: [keys: string[], label: string][] }[] = [
  {
    title: '全局',
    items: [
      [[MOD, 'K'], '搜索历史会话（↑↓ 选择，Enter 打开）'],
      [[MOD, 'Shift', 'O'], '新建分析'],
      [['/'], '聚焦主输入框'],
      [['?'], '打开本面板'],
    ],
  },
  {
    title: '对话',
    items: [
      [['Enter'], '发送（分析进行中时排队）'],
      [['Shift', 'Enter'], '换行'],
      [['↑', '↓'], '输入框为空时翻看之前问过的问题'],
    ],
  },
  {
    title: '报告与原文',
    items: [
      [['['], '上一条证据'],
      [[']'], '下一条证据'],
      [['Enter'], '原文查找：下一个匹配（Shift+Enter 上一个）'],
      [['Esc'], '关闭面板 / 弹窗'],
    ],
  },
]

export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null)
  const onKeyDown = useModal(dialog, onClose)
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[10vh]" onClick={onClose}>
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md space-y-4 rounded-2xl border border-zinc-200 bg-white p-5 shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div className="flex items-center justify-between">
          <h2 id="shortcuts-title" className="text-base font-semibold text-zinc-900 dark:text-zinc-50">
            键盘快捷键
          </h2>
          <Button variant="ghost" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>
        {GROUPS.map((group) => (
          <section key={group.title} className="space-y-1.5">
            <h3 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{group.title}</h3>
            <dl className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {group.items.map(([keys, label]) => (
                <div key={label} className="flex items-center justify-between gap-4 py-1.5 text-sm">
                  <dt className="text-zinc-700 dark:text-zinc-300">{label}</dt>
                  <dd className="flex shrink-0 gap-1">
                    {keys.map((key) => (
                      <kbd
                        key={key}
                        className="min-w-6 rounded-md border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-center font-sans text-xs text-zinc-600 shadow-xs dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                      >
                        {key}
                      </kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
  )
}

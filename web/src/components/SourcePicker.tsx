import { FileText, FolderCode, Plus, X } from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'
import { baseName } from '../lib/format'
import { FileBrowser } from './FileBrowser'

function parentOf(path: string): string {
  const index = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return index > 0 ? path.slice(0, index) : path
}

export function SourcePicker({
  kind,
  values,
  onChange,
}: {
  kind: 'log' | 'code'
  values: string[]
  onChange: (next: string[]) => void
}) {
  const [browsing, setBrowsing] = useState(false)
  const [typed, setTyped] = useState('')
  const isLog = kind === 'log'
  const Icon = isLog ? FileText : FolderCode

  function add(paths: string[]) {
    onChange([...new Set([...values, ...paths.map((p) => p.trim()).filter(Boolean)])])
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    if (typed.trim()) {
      add([typed])
      setTyped('')
    }
  }

  const last = values.at(-1)
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium text-zinc-800 dark:text-zinc-100">
          {isLog ? '日志文件' : '源码目录'}
          {isLog ? <span className="ml-1 text-red-500">*</span> : <span className="ml-1.5 text-xs font-normal text-zinc-400">可选</span>}
        </h3>
        <span className="text-xs text-zinc-400">{isLog ? '支持多个文件、通配符' : '提供后 agent 会结合代码定位根因'}</span>
      </div>

      {values.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {values.map((value) => (
            <li
              key={value}
              title={value}
              className="flex max-w-full items-center gap-1.5 rounded-md border border-zinc-200 bg-zinc-50 py-1 pl-2 pr-1 text-xs dark:border-zinc-700 dark:bg-zinc-800/60"
            >
              <Icon className="h-3.5 w-3.5 shrink-0 text-zinc-400" aria-hidden />
              <span className="font-medium">{baseName(value) || value}</span>
              <span className="hidden min-w-0 truncate font-mono text-zinc-400 sm:inline">{parentOf(value)}</span>
              <button
                type="button"
                onClick={() => onChange(values.filter((v) => v !== value))}
                aria-label={`移除 ${value}`}
                className="rounded p-0.5 text-zinc-400 hover:bg-zinc-200 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setBrowsing(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-dashed border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:border-sky-500 hover:bg-sky-50 hover:text-sky-700 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-sky-950/30 dark:hover:text-sky-300"
        >
          <Plus className="h-4 w-4" aria-hidden />
          {isLog ? '浏览文件' : '浏览目录'}
        </button>
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => {
            if (typed.trim()) {
              add([typed])
              setTyped('')
            }
          }}
          spellCheck={false}
          aria-label={isLog ? '输入日志路径' : '输入源码目录路径'}
          placeholder={isLog ? '或粘贴路径，如 ~/logs/app.log、/var/log/*.log，回车添加' : '或粘贴目录路径，如 ~/work/order-service，回车添加'}
          className="min-w-0 flex-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 font-mono text-xs outline-none placeholder:font-sans focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 dark:border-zinc-700 dark:bg-zinc-950"
        />
      </div>

      {browsing && (
        <FileBrowser
          mode={isLog ? 'file' : 'dir'}
          title={isLog ? '选择日志文件' : '选择源码目录'}
          initialPath={last ? (isLog ? parentOf(last) : last) : undefined}
          onClose={() => setBrowsing(false)}
          onConfirm={(paths) => {
            add(paths)
            setBrowsing(false)
          }}
        />
      )}
    </div>
  )
}

import { useQueries } from '@tanstack/react-query'
import { CircleAlert, CircleCheck, FileText, FolderGit2, FolderOpen, History, Plus, X } from 'lucide-react'
import { useEffect, useState, type KeyboardEvent } from 'react'
import { api } from '../lib/api'
import { baseName } from '../lib/format'
import type { FsGlob } from '../lib/types'
import { FileBrowser } from './FileBrowser'
import { Spinner } from './ui'

function parentOf(path: string): string {
  const index = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return index > 0 ? path.slice(0, index) : path
}

/** 多行粘贴：去掉空行和包裹路径的引号（终端「复制路径」常带引号） */
export function splitPaths(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^(['"])(.*)\1$/, '$2'))
    .filter(Boolean)
}

export interface SourceValidity {
  invalid: number
  checking: number
}

type PathStatus = { state: 'checking' } | { state: 'ok'; matched: number } | { state: 'error'; message: string }

function statusOf(isLog: boolean, data: FsGlob | undefined, error: unknown, pending: boolean): PathStatus {
  if (pending) return { state: 'checking' }
  if (error) return { state: 'error', message: error instanceof Error ? error.message : String(error) }
  if (!data) return { state: 'checking' }
  if (isLog && data.files.length === 0) {
    return { state: 'error', message: '这是一个目录：请选择其中的日志文件，或写成 目录/*.log' }
  }
  if (!isLog && data.files.length > 0) return { state: 'error', message: '这是一个文件：请填写源码所在的目录' }
  return { state: 'ok', matched: data.files.length }
}

const INPUT_ICON_BUTTON =
  'flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40'

export function SourcePicker({
  kind,
  values,
  onChange,
  onValidity,
  recent = [],
}: {
  kind: 'log' | 'code'
  values: string[]
  onChange: (next: string[]) => void
  onValidity?: (validity: SourceValidity) => void
  /** 之前会话里用过的路径，点一下就加进来 */
  recent?: string[]
}) {
  const [browsing, setBrowsing] = useState(false)
  const suggestions = recent.filter((path) => !values.includes(path)).slice(0, 3)
  const [typed, setTyped] = useState('')
  const isLog = kind === 'log'
  const Icon = isLog ? FileText : FolderGit2
  const label = isLog ? '日志文件' : '源码目录'
  const inputId = `${kind}-path-input`

  // 添加时就去服务端确认路径存在，而不是等点「开始分析」才报错
  const checks = useQueries({
    queries: values.map((path) => ({
      queryKey: ['fs-glob', path],
      queryFn: () => api.fsGlob(path),
      retry: false,
      staleTime: 30_000,
    })),
  })
  const statuses = values.map((_, i) => statusOf(isLog, checks[i]?.data, checks[i]?.error, !!checks[i]?.isPending))
  const invalid = statuses.filter((s) => s.state === 'error').length
  const checking = statuses.filter((s) => s.state === 'checking').length

  useEffect(() => {
    onValidity?.({ invalid, checking })
  }, [invalid, checking]) // eslint-disable-line react-hooks/exhaustive-deps

  function add(paths: string[]) {
    onChange([...new Set([...values, ...paths.map((p) => p.trim()).filter(Boolean)])])
  }

  function commitTyped() {
    if (typed.trim()) {
      add([typed])
      setTyped('')
    }
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    commitTyped()
  }

  const last = values.at(-1)

  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center gap-2">
        <label htmlFor={inputId} className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
          <Icon className="size-3.5 text-muted-foreground" aria-hidden />
          {label}
        </label>
        <span className={`rounded px-1.5 text-xs ${isLog ? 'bg-brand-soft text-brand-strong' : 'bg-muted text-muted-foreground'}`}>
          {isLog ? '必填' : '可选'}
        </span>
      </div>

      {values.length > 0 && (
        <ul className="mb-2 space-y-1.5">
          {values.map((value, i) => {
            const status = statuses[i]
            const bad = status.state === 'error'
            return (
              <li key={value}>
                <div
                  title={value}
                  className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 ${
                    bad ? 'border-red-300 bg-red-50 dark:border-red-500/40 dark:bg-red-500/10' : 'border-border bg-background'
                  }`}
                >
                  {status.state === 'checking' ? (
                    <Spinner className="shrink-0" />
                  ) : bad ? (
                    <CircleAlert className="size-3.5 shrink-0 text-red-600 dark:text-red-400" aria-hidden />
                  ) : (
                    <CircleCheck className="size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                  )}
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]">
                    <span className="text-foreground">{baseName(value) || value}</span>
                    <span className="ml-1.5 text-muted-foreground">{parentOf(value)}</span>
                  </span>
                  {status.state === 'ok' && (
                    <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
                      {isLog ? (status.matched > 1 ? `匹配 ${status.matched} 个文件` : '1 个文件') : '目录'}
                    </span>
                  )}
                  <span className="sr-only">{bad ? `，有问题：${status.message}` : status.state === 'checking' ? '，正在检查' : ''}</span>
                  <button
                    type="button"
                    onClick={() => onChange(values.filter((v) => v !== value))}
                    aria-label={`移除 ${value}`}
                    className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
                {bad && (
                  <p role="alert" className="mt-1 break-all pl-1 text-xs text-red-700 dark:text-red-300">
                    {status.message}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}

      <div className="relative">
        <input
          id={inputId}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={onKey}
          onBlur={commitTyped}
          onPaste={(e) => {
            // 一次粘贴多行路径（比如从终端 ls 的输出复制）时逐行添加，而不是挤成一个路径
            const lines = splitPaths(e.clipboardData.getData('text'))
            if (lines.length < 2) return
            e.preventDefault()
            add(lines)
          }}
          spellCheck={false}
          aria-label={isLog ? '输入日志路径' : '输入源码目录路径'}
          placeholder={isLog ? '~/logs/app.log 或 /var/log/*.log' : '~/work/order-service'}
          className="h-9 w-full rounded-lg border border-input bg-background pl-3 pr-16 font-mono text-[13px] outline-none placeholder:font-sans placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
        />
        <div className="absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5">
          <button
            type="button"
            onClick={() => setBrowsing(true)}
            aria-label={isLog ? '浏览文件' : '浏览目录'}
            title={isLog ? '浏览文件' : '浏览目录'}
            className={INPUT_ICON_BUTTON}
          >
            <FolderOpen className="size-4" />
          </button>
          <button type="button" onClick={commitTyped} disabled={!typed.trim()} aria-label={`添加${label}`} className={INPUT_ICON_BUTTON}>
            <Plus className="size-4" />
          </button>
        </div>
      </div>

      {suggestions.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <History className="size-3" aria-hidden />
            最近使用
          </span>
          {suggestions.map((path) => (
            <button
              key={path}
              type="button"
              onClick={() => add([path])}
              title={`添加 ${path}`}
              className="max-w-full truncate rounded-md border border-border bg-background px-2 py-0.5 font-mono text-xs text-foreground/80 transition-colors hover:border-primary/40 hover:bg-brand-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {baseName(path) || path}
            </button>
          ))}
        </div>
      ) : (
        values.length === 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            {isLog ? '支持多个文件、通配符和多行粘贴' : '提供后 agent 会结合代码定位根因'}
          </p>
        )
      )}

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

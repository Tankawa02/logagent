import { useQueries } from '@tanstack/react-query'
import { AlertCircle, FileText, FolderCode, History, Plus, X } from 'lucide-react'
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
  const suggestions = recent.filter((path) => !values.includes(path))
  const [typed, setTyped] = useState('')
  const isLog = kind === 'log'
  const Icon = isLog ? FileText : FolderCode

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
  const errors = values.flatMap((value, i) => {
    const status = statuses[i]
    return status.state === 'error' ? [{ value, message: status.message }] : []
  })

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <h3 className="flex items-center gap-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-300">
          <Icon className="h-3.5 w-3.5 self-center text-zinc-400" aria-hidden />
          {isLog ? '日志文件' : '源码目录'}
          {isLog ? (
            <span className="rounded-full bg-brand-50 px-1.5 py-px text-[11px] font-medium text-brand-700 dark:bg-brand-950/50 dark:text-brand-300">
              必填
            </span>
          ) : (
            <span className="font-normal text-zinc-500 dark:text-zinc-400">可选</span>
          )}
        </h3>
        {/* 选好之后说明文字就没用了，收起来让来源区更紧凑 */}
        {values.length === 0 && (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{isLog ? '支持多个文件、通配符' : '提供后 agent 会结合代码定位根因'}</p>
        )}
      </div>

      {values.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {values.map((value, i) => {
            const status = statuses[i]
            const bad = status.state === 'error'
            return (
              <li
                key={value}
                title={bad ? `${value}\n${status.message}` : value}
                className={`flex max-w-full items-center gap-1.5 rounded-full border py-1 pl-2.5 pr-1 text-xs shadow-xs ${
                  bad
                    ? 'border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200'
                    : 'border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-900'
                }`}
              >
                {status.state === 'checking' ? (
                  <Spinner className="shrink-0" />
                ) : bad ? (
                  <AlertCircle className="h-3.5 w-3.5 shrink-0 text-red-600 dark:text-red-400" aria-hidden />
                ) : (
                  <Icon className="h-3.5 w-3.5 shrink-0 text-zinc-400" aria-hidden />
                )}
                <span className="font-medium">{baseName(value) || value}</span>
                <span className={`hidden min-w-0 truncate font-mono sm:inline ${bad ? 'text-red-500/80' : 'text-zinc-400'}`}>
                  {parentOf(value)}
                </span>
                {status.state === 'ok' && status.matched > 1 && (
                  <span className="shrink-0 rounded-full bg-zinc-100 px-1.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                    {status.matched} 个文件
                  </span>
                )}
                <span className="sr-only">{bad ? `，有问题：${status.message}` : status.state === 'checking' ? '，正在检查' : ''}</span>
                <button
                  type="button"
                  onClick={() => onChange(values.filter((v) => v !== value))}
                  aria-label={`移除 ${value}`}
                  className={`rounded-full p-0.5 ${
                    bad
                      ? 'text-red-500 hover:bg-red-100 hover:text-red-800 dark:hover:bg-red-900/60 dark:hover:text-red-100'
                      : 'text-zinc-400 hover:bg-zinc-200 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200'
                  }`}
                >
                  <X className="h-3 w-3" />
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {errors.length > 0 && (
        <ul role="alert" className="space-y-0.5 px-0.5 text-xs text-red-700 dark:text-red-300">
          {errors.map(({ value, message }) => (
            <li key={value} className="break-all">
              <span className="font-medium">{baseName(value) || value}</span>：{message}
            </li>
          ))}
        </ul>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setBrowsing(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 shadow-xs transition-colors hover:border-brand-300 hover:text-brand-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:text-brand-300"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {isLog ? '浏览文件' : '浏览目录'}
        </button>
        <input
          id={`${kind}-path-input`}
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
          placeholder={isLog ? '或粘贴路径（可多行），回车添加' : '或粘贴目录路径（可多行），回车添加'}
          className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2.5 py-1.5 font-mono text-xs outline-none transition-colors placeholder:font-sans placeholder:text-zinc-400 hover:border-zinc-200 focus:border-brand-400 focus:bg-white focus:ring-4 focus:ring-brand-500/10 dark:hover:border-zinc-700 dark:focus:bg-zinc-950"
        />
      </div>
      {suggestions.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5 px-0.5">
          <span className="flex items-center gap-1 text-xs text-zinc-500 dark:text-zinc-400">
            <History className="h-3 w-3" aria-hidden />
            最近使用
          </span>
          {suggestions.map((path) => (
            <button
              key={path}
              type="button"
              onClick={() => add([path])}
              title={`添加 ${path}`}
              className="flex max-w-56 items-center gap-1 rounded-full border border-dashed border-zinc-300 px-2 py-0.5 text-xs text-zinc-600 transition-colors hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700 dark:border-zinc-700 dark:text-zinc-300 dark:hover:border-brand-800 dark:hover:bg-brand-950/40 dark:hover:text-brand-300"
            >
              <Plus className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate">{baseName(path) || path}</span>
            </button>
          ))}
        </div>
      ) : (
        values.length === 0 && (
          <p className="flex flex-wrap gap-x-1.5 gap-y-0.5 px-0.5 text-xs text-zinc-400 dark:text-zinc-500">
            <span>例如</span>
            {(isLog ? ['~/logs/app.log', '/var/log/*.log'] : ['~/work/order-service']).map((example) => (
              <code key={example} className="break-all font-mono">
                {example}
              </code>
            ))}
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

import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import {
  ArrowUp,
  Check,
  ChevronRight,
  Clock,
  FileText,
  Folder,
  FolderCode,
  HardDrive,
  Home,
  Search,
  Terminal,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../lib/api'
import type { FsEntry, Place } from '../lib/types'
import { Button, ErrorBox, Spinner } from './ui'

const PLACE_ICON: Record<Place['kind'], typeof Home> = {
  home: Home,
  cwd: Terminal,
  'recent-log': Clock,
  'recent-code': FolderCode,
  drive: HardDrive,
  root: HardDrive,
}

const LOG_HINT = /\.(log|txt|out|err|jsonl?|gz|csv)$|\.log\.\d+$/i
const GLOB_CHARS = /[*?[]/

export function formatSize(size: number | null): string {
  if (size === null) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`
  return `${(size / 1024 ** 3).toFixed(2)} GB`
}

function formatMtime(seconds: number): string {
  const d = new Date(seconds * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function crumbs(path: string, sep: string): { label: string; path: string }[] {
  const windows = sep === '\\'
  const parts = path.split(/[\\/]/).filter(Boolean)
  const result: { label: string; path: string }[] = []
  let acc = windows ? '' : '/'
  if (!windows) result.push({ label: '/', path: '/' })
  parts.forEach((part, i) => {
    acc = windows ? (i === 0 ? `${part}\\` : `${acc}${acc.endsWith('\\') ? '' : '\\'}${part}`) : `${acc}${acc.endsWith('/') ? '' : '/'}${part}`
    result.push({ label: part, path: acc })
  })
  return result
}

export function FileBrowser({
  mode,
  title,
  initialPath,
  onConfirm,
  onClose,
}: {
  mode: 'file' | 'dir'
  title: string
  initialPath?: string
  onConfirm: (paths: string[]) => void
  onClose: () => void
}) {
  const [path, setPath] = useState(initialPath ?? '')
  const [typed, setTyped] = useState(initialPath ?? '')
  const [filter, setFilter] = useState('')
  const [serverFilter, setServerFilter] = useState('')
  const [hidden, setHidden] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [globError, setGlobError] = useState<unknown>(null)

  useEffect(() => {
    const timer = setTimeout(() => setServerFilter(filter.trim()), 250)
    return () => clearTimeout(timer)
  }, [filter])

  const places = useQuery({ queryKey: ['places'], queryFn: api.places, staleTime: 60_000 })
  // 筛选词交给服务端，在截断到 2000 条之前生效，大目录里靠后的条目也能搜到
  const listing = useQuery({
    queryKey: ['fs', path, hidden, serverFilter, mode],
    queryFn: () => api.fsList(path, { hidden, q: serverFilter, dirs: mode === 'dir' }),
    placeholderData: keepPreviousData,
    retry: false,
  })
  // 正在换目录时手里的还是上一个目录的列表，不能拿它去「选择此目录」
  const listingStale = listing.isPlaceholderData || listing.isFetching

  const expand = useMutation({
    mutationFn: (pattern: string) => api.fsGlob(pattern),
    onSuccess: (result) => {
      // 部分匹配不能悄悄当成全部：一个都不勾，让用户把通配符写具体
      if (result.truncated) {
        setGlobError(new Error(`匹配到的文件超过 ${result.files.length} 个，没有勾选。请把通配符写得更具体。`))
        return
      }
      setGlobError(null)
      // 路径本身存在（比如目录名里带 [ ]）时服务端按字面返回：目录只跳转，文件才勾选
      if (result.files.length) setPicked((list) => [...new Set([...list, ...result.files])])
      setFilter('')
      setServerFilter('')
      setPath(result.dir)
    },
    onError: (error) => setGlobError(error),
  })

  useEffect(() => {
    if (listing.data) setTyped(listing.data.path)
  }, [listing.data?.path]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const entries = useMemo(() => {
    const all = listing.data?.entries ?? []
    const visible = mode === 'dir' ? all.filter((e) => e.kind === 'dir') : all
    const q = filter.trim().toLowerCase()
    return q ? visible.filter((e) => e.name.toLowerCase().includes(q)) : visible
  }, [listing.data, mode, filter])

  function open(next: string) {
    setFilter('')
    setServerFilter('')
    setGlobError(null)
    setPath(next)
  }

  function togglePick(entry: FsEntry) {
    setPicked((list) => (list.includes(entry.path) ? list.filter((p) => p !== entry.path) : [...list, entry.path]))
  }

  function onGo(event: FormEvent) {
    event.preventDefault()
    // 对话框在 React 树里位于新建分析的表单之内，提交事件会继续冒泡到外层表单
    event.stopPropagation()
    const target = typed.trim()
    if (!target) return
    // 带通配字符的交给服务端：真实存在的路径（如 app[old]）按字面打开，否则展开成文件并勾选
    if (mode === 'file' && GLOB_CHARS.test(target)) expand.mutate(target)
    else open(target)
  }

  function confirm() {
    if (mode === 'dir') {
      if (listing.data && !listingStale) onConfirm([listing.data.path])
    } else if (picked.length) {
      onConfirm(picked)
    }
  }

  const sep = listing.data?.sep ?? '/'
  const allLogsHere = entries.filter((e) => e.kind === 'file' && LOG_HINT.test(e.name))

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-6" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        className="flex h-[92dvh] w-full max-w-4xl flex-col overflow-hidden rounded-t-xl border border-zinc-200 bg-white shadow-2xl sm:h-[78vh] sm:rounded-xl dark:border-zinc-800 dark:bg-zinc-900"
      >
        <header className="flex items-center justify-between gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <div>
            <h2 className="text-sm font-semibold">{title}</h2>
            <p className="text-xs text-zinc-500">
              {mode === 'dir' ? '进入要分析的源码目录，然后点「选择此目录」' : '勾选一个或多个日志文件；也可以在路径栏直接输入通配符，如 /var/log/app/*.log'}
            </p>
          </div>
          <Button variant="ghost" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="flex min-h-0 flex-1">
          <nav aria-label="快捷位置" className="hidden w-48 shrink-0 space-y-0.5 overflow-auto border-r border-zinc-200 p-2 sm:block dark:border-zinc-800">
            {places.data?.map((place) => {
              const Icon = PLACE_ICON[place.kind] ?? Folder
              const active = listing.data?.path === place.path
              return (
                <button
                  key={place.path}
                  type="button"
                  onClick={() => open(place.path)}
                  title={place.path}
                  className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                    active ? 'bg-zinc-100 font-medium dark:bg-zinc-800' : 'text-zinc-600 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800/60'
                  }`}
                >
                  <Icon className="h-4 w-4 shrink-0 text-zinc-400" aria-hidden />
                  <span className="truncate">{place.label}</span>
                </button>
              )
            })}
          </nav>

          <div className="flex min-w-0 flex-1 flex-col">
            <form onSubmit={onGo} className="flex items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
              <Button
                variant="ghost"
                onClick={() => listing.data?.parent && open(listing.data.parent)}
                disabled={!listing.data?.parent}
                aria-label="上一级"
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
              <label className="sr-only" htmlFor="fs-path">
                路径
              </label>
              <input
                id="fs-path"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                spellCheck={false}
                className="min-w-0 flex-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 font-mono text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-700 dark:bg-zinc-950"
              />
              <Button type="submit" disabled={expand.isPending}>
                {expand.isPending ? <Spinner /> : mode === 'file' && GLOB_CHARS.test(typed) ? '匹配' : '前往'}
              </Button>
            </form>
            {globError != null && (
              <div className="px-3 pt-2">
                <ErrorBox error={globError} />
              </div>
            )}

            {listing.data && (
              <div className="flex items-center gap-0.5 overflow-x-auto px-3 py-1.5 text-xs text-zinc-500">
                {crumbs(listing.data.path, sep).map((c, i, list) => (
                  <span key={c.path} className="flex shrink-0 items-center gap-0.5">
                    <button type="button" onClick={() => open(c.path)} className="rounded px-1 py-0.5 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100">
                      {c.label}
                    </button>
                    {i < list.length - 1 && <ChevronRight className="h-3 w-3" aria-hidden />}
                  </span>
                ))}
              </div>
            )}

            <div className="flex items-center gap-2 px-3 pb-2">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" aria-hidden />
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="在当前目录里筛选"
                  aria-label="筛选"
                  className="w-full rounded-md border border-zinc-200 bg-zinc-50 py-1 pl-7 pr-2 text-xs outline-none focus:border-brand-500 dark:border-zinc-800 dark:bg-zinc-950"
                />
              </div>
              <label className="flex shrink-0 items-center gap-1.5 text-xs text-zinc-500">
                <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} className="accent-brand-600" />
                显示隐藏文件
              </label>
              {mode === 'file' && allLogsHere.length > 1 && (
                <Button
                  variant="ghost"
                  className="shrink-0 text-xs"
                  onClick={() => setPicked((list) => [...new Set([...list, ...allLogsHere.map((e) => e.path)])])}
                >
                  全选日志
                </Button>
              )}
            </div>

            <div className="min-h-0 flex-1 overflow-auto border-t border-zinc-100 dark:border-zinc-800">
              {listing.error && (
                <div className="p-3">
                  <ErrorBox error={listing.error} />
                </div>
              )}
              {listing.isLoading && (
                <div className="flex justify-center p-10">
                  <Spinner />
                </div>
              )}
              {listing.data && entries.length === 0 && (
                <p className="p-8 text-center text-sm text-zinc-500">{filter ? '没有匹配的条目' : mode === 'dir' ? '这里没有子目录' : '空目录'}</p>
              )}
              <ul className={listing.isFetching && !listing.isLoading ? 'opacity-60' : ''}>
                {entries.map((entry) => {
                  const isDir = entry.kind === 'dir'
                  const selected = picked.includes(entry.path)
                  return (
                    <li key={entry.path}>
                      <button
                        type="button"
                        onClick={() => (isDir ? open(entry.path) : togglePick(entry))}
                        onDoubleClick={() => !isDir && onConfirm([...new Set([...picked, entry.path])])}
                        aria-pressed={isDir ? undefined : selected}
                        className={`flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm ${
                          selected ? 'bg-brand-50 dark:bg-brand-950/40' : 'hover:bg-zinc-50 dark:hover:bg-zinc-800/50'
                        }`}
                      >
                        {!isDir && (
                          <span
                            aria-hidden
                            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                              selected ? 'border-brand-600 bg-brand-600 text-white' : 'border-zinc-300 dark:border-zinc-600'
                            }`}
                          >
                            {selected && <Check className="h-3 w-3" />}
                          </span>
                        )}
                        {isDir ? (
                          <Folder className="h-4 w-4 shrink-0 text-brand-600 dark:text-brand-400" aria-hidden />
                        ) : (
                          <FileText className={`h-4 w-4 shrink-0 ${LOG_HINT.test(entry.name) ? 'text-zinc-600 dark:text-zinc-300' : 'text-zinc-400'}`} aria-hidden />
                        )}
                        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                        <span className="hidden shrink-0 text-xs tabular-nums text-zinc-400 sm:inline">{formatMtime(entry.mtime)}</span>
                        <span className="w-20 shrink-0 text-right text-xs tabular-nums text-zinc-400">{formatSize(entry.size)}</span>
                        {isDir && <ChevronRight className="h-4 w-4 shrink-0 text-zinc-300" aria-hidden />}
                      </button>
                    </li>
                  )
                })}
              </ul>
              {listing.data?.truncated && (
                <p className="p-3 text-center text-xs text-zinc-400">
                  {listing.data.query
                    ? `匹配「${listing.data.query}」的有 ${listing.data.total ?? '很多'} 个，只显示前 ${listing.data.entries.length} 个，可以把筛选词写得更具体。`
                    : `共 ${listing.data.total ?? '很多'} 个条目，只显示前 ${listing.data.entries.length} 个；用上方筛选可在整个目录里查找。`}
                </p>
              )}
            </div>
          </div>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <p className="min-w-0 truncate text-xs text-zinc-500">
            {mode === 'dir' ? (
              <>
                将选择：
                <span className="font-mono text-zinc-700 dark:text-zinc-300">{listingStale || !listing.data ? '正在打开…' : listing.data.path}</span>
              </>
            ) : picked.length ? (
              `已选 ${picked.length} 个文件`
            ) : (
              '点击文件勾选，双击直接添加'
            )}
          </p>
          <div className="flex gap-2">
            <Button onClick={onClose}>取消</Button>
            <Button
              variant="primary"
              onClick={confirm}
              disabled={mode === 'file' ? !picked.length : !listing.data || listingStale || !!listing.error}
            >
              {mode === 'dir' ? '选择此目录' : `添加${picked.length ? ` ${picked.length} 个` : ''}`}
            </Button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  )
}

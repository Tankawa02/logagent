import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, Lightbulb, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { useMemo, useState, type KeyboardEvent } from 'react'
import { CandidateRow, KIND_TONE, MEMORY_FIELD, SimilarPrompt, useRefreshMemory } from '../components/MemoryCandidate'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Spinner } from '../components/ui'
import { api } from '../lib/api'
import type { MemoryIndex, MemoryItem, MemoryKind, MemorySaveResult } from '../lib/types'

const FIELD = MEMORY_FIELD
const GLOBAL = '__global__'
const ORIGIN_LABEL: Record<string, string> = { explicit: '手动保存', suggested: '采纳的建议' }

function scopeValue(project: string | null): string {
  return project ?? GLOBAL
}

function isComposing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229
}

function AddMemory({ index }: { index: MemoryIndex }) {
  const refresh = useRefreshMemory()
  const [text, setText] = useState('')
  const [kind, setKind] = useState<MemoryKind>('preference')
  const [scope, setScope] = useState(GLOBAL)
  const [similar, setSimilar] = useState<MemoryItem[] | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: (extra: { replace_id?: number; force?: boolean }) =>
      api.addMemory({ text: text.trim(), kind, project: scope === GLOBAL ? null : scope, ...extra }),
    onSuccess: (result: MemorySaveResult) => {
      if (!result.saved) {
        setSimilar(result.similar)
        return
      }
      setSimilar(null)
      setText('')
      setNotice(result.updated ? `已更新 #${result.memory.id}` : `已记住 #${result.memory.id}`)
      refresh()
    },
  })

  const submit = (event: { preventDefault(): void }) => {
    event.preventDefault()
    if (text.trim()) {
      setNotice(null)
      save.mutate({})
    }
  }

  return (
    <Card>
      <CardHeader title="新增记忆" />
      <form onSubmit={submit} className="space-y-3 px-4 py-4">
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setSimilar(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !isComposing(e)) submit(e)
          }}
          rows={3}
          maxLength={300}
          placeholder="例如：报告先写结论再写证据；“网关”指 account-gateway；测试环境叫 pre"
          aria-label="记忆内容"
          className={FIELD}
        />
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label htmlFor="memory-kind" className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
              类型
            </label>
            <select
              id="memory-kind"
              value={kind}
              onChange={(e) => {
                const next = e.target.value as MemoryKind
                setKind(next)
                // 表达偏好默认全局；术语、事实挂在项目上，与 CLI 的默认归类一致
                if (next === 'preference') setScope(GLOBAL)
                else if (scope === GLOBAL && index.projects.length === 1) setScope(index.projects[0].key)
              }}
              className={FIELD}
            >
              {index.kinds.map((k) => (
                <option key={k.key} value={k.key}>
                  {k.label}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-48 flex-1 space-y-1">
            <label htmlFor="memory-scope" className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
              范围
            </label>
            <select id="memory-scope" value={scope} onChange={(e) => setScope(e.target.value)} className={FIELD}>
              <option value={GLOBAL}>全局 · 所有项目生效</option>
              {index.projects.map((p) => (
                <option key={p.key} value={p.key} title={p.key}>
                  {p.label} · 仅该项目
                </option>
              ))}
            </select>
          </div>
          <Button type="submit" variant="primary" disabled={!text.trim() || save.isPending}>
            {save.isPending ? <Spinner className="h-3 w-3" /> : <Plus className="h-4 w-4" aria-hidden />}
            保存
          </Button>
        </div>
        <p className="text-xs text-zinc-400">内容会脱敏后保存，最长 300 字。⌘ / Ctrl + Enter 快速保存。</p>
        {similar && (
          <SimilarPrompt
            similar={similar}
            busy={save.isPending}
            onReplace={(id) => save.mutate({ replace_id: id })}
            onForce={() => save.mutate({ force: true })}
            onCancel={() => setSimilar(null)}
          />
        )}
        {save.error && <ErrorBox error={save.error} />}
        {notice && (
          <p role="status" className="flex items-center gap-1 text-xs text-emerald-600">
            <Check className="h-3.5 w-3.5" aria-hidden />
            {notice}
          </p>
        )}
      </form>
    </Card>
  )
}

function MemoryRow({ memory }: { memory: MemoryItem }) {
  const refresh = useRefreshMemory()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(memory.text)
  const update = useMutation({
    mutationFn: () => api.updateMemory(memory.id, text.trim()),
    onSuccess: () => {
      setEditing(false)
      refresh()
    },
  })
  const remove = useMutation({ mutationFn: () => api.deleteMemory(memory.id), onSuccess: refresh })

  return (
    <li className="group flex items-start gap-3 px-4 py-3">
      <span className="w-8 shrink-0 pt-0.5 text-right font-mono text-xs text-zinc-400">#{memory.id}</span>
      <div className="min-w-0 flex-1 space-y-1.5">
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (text.trim() && text.trim() !== memory.text) update.mutate()
              else setEditing(false)
            }}
            className="space-y-2"
          >
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setEditing(false)
              }}
              rows={2}
              maxLength={300}
              autoFocus
              aria-label={`修改记忆 #${memory.id}`}
              className={FIELD}
            />
            <div className="flex gap-2">
              <Button type="submit" variant="primary" className="px-2 py-1 text-xs" disabled={update.isPending || !text.trim()}>
                保存
              </Button>
              <Button
                className="px-2 py-1 text-xs"
                onClick={() => {
                  setText(memory.text)
                  setEditing(false)
                }}
              >
                取消
              </Button>
            </div>
          </form>
        ) : (
          <p className="whitespace-pre-wrap break-words text-sm text-zinc-800 dark:text-zinc-100">{memory.text}</p>
        )}
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-400">
          <Badge tone={KIND_TONE[memory.kind]}>{memory.kind_label}</Badge>
          <Badge title={memory.project ?? '对所有项目生效'}>{memory.scope_label}</Badge>
          <span>{ORIGIN_LABEL[memory.origin] ?? memory.origin}</span>
          <span aria-hidden>·</span>
          <span>{memory.updated_at.slice(0, 16)}</span>
        </div>
        {(update.error || remove.error) && <ErrorBox error={update.error ?? remove.error} />}
      </div>
      {!editing && (
        <div className="flex shrink-0 gap-0.5 opacity-0 focus-within:opacity-100 group-hover:opacity-100">
          <button
            type="button"
            onClick={() => setEditing(true)}
            aria-label={`编辑记忆 #${memory.id}`}
            className="rounded p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`删除记忆 #${memory.id}？下一轮对话起不再使用。`)) remove.mutate()
            }}
            aria-label={`删除记忆 #${memory.id}`}
            className="rounded p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-red-600 dark:hover:bg-zinc-800"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </li>
  )
}

function MemoryList({ index }: { index: MemoryIndex }) {
  const [kind, setKind] = useState<MemoryKind | ''>('')
  const [scope, setScope] = useState('')
  const [q, setQ] = useState('')
  const scopes = useMemo(() => {
    const map = new Map<string, string>()
    for (const m of index.memories) map.set(scopeValue(m.project), m.scope_label)
    return [...map.entries()]
  }, [index.memories])
  const items = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return index.memories.filter(
      (m) =>
        (!kind || m.kind === kind) && (!scope || scopeValue(m.project) === scope) && (!needle || m.text.toLowerCase().includes(needle)),
    )
  }, [index.memories, kind, scope, q])
  const counts = useMemo(() => {
    const result: Record<string, number> = {}
    for (const m of index.memories) result[m.kind] = (result[m.kind] ?? 0) + 1
    return result
  }, [index.memories])

  return (
    <Card>
      <CardHeader title={`已保存的记忆（${index.memories.length}）`}>
        <div role="tablist" aria-label="按类型筛选" className="flex rounded-md bg-zinc-100 p-0.5 text-xs dark:bg-zinc-800">
          {[{ key: '' as const, label: '全部' }, ...index.kinds].map((k) => (
            <button
              key={k.key || 'all'}
              type="button"
              role="tab"
              aria-selected={kind === k.key}
              onClick={() => setKind(k.key)}
              className={`rounded px-2 py-1 ${kind === k.key ? 'bg-white font-medium text-zinc-900 shadow-xs dark:bg-zinc-950 dark:text-zinc-100' : 'text-zinc-500'}`}
            >
              {k.label}
              {k.key && counts[k.key] ? <span className="ml-1 text-zinc-400">{counts[k.key]}</span> : null}
            </button>
          ))}
        </div>
      </CardHeader>
      {index.memories.length > 0 && (
        <div className="flex flex-wrap gap-2 border-b border-zinc-100 px-4 py-2.5 dark:border-zinc-800">
          <div className="relative min-w-48 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" aria-hidden />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索记忆内容"
              aria-label="搜索记忆"
              className={`${FIELD} pl-8`}
            />
          </div>
          {scopes.length > 1 && (
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              aria-label="按范围筛选"
              className={FIELD.replace('w-full', 'w-auto shrink-0')}
            >
              <option value="">全部范围</option>
              {scopes.map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      {index.memories.length === 0 ? (
        <Empty>还没有记忆。在上方新增，或在 chat 里说&ldquo;记住……&rdquo;。</Empty>
      ) : items.length === 0 ? (
        <Empty>没有符合筛选条件的记忆</Empty>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {items.map((m) => (
            <MemoryRow key={`${m.id}-${m.updated_at}`} memory={m} />
          ))}
        </ul>
      )}
    </Card>
  )
}

export function MemoryPage() {
  const memory = useQuery({ queryKey: ['memory'], queryFn: api.memory })
  const data = memory.data

  return (
    <main className="h-full overflow-auto">
      <div className="mx-auto max-w-4xl space-y-5 px-4 py-6 sm:px-6">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">Memory</h1>
          <p className="mt-1 text-sm text-zinc-500">
            跨会话记住你的表达偏好、术语和项目事实，每轮对话前注入给模型。表达偏好默认全局，术语和事实挂在项目上。
          </p>
          {data && (
            <p className="mt-1 truncate font-mono text-xs text-zinc-400" title={data.path}>
              {data.path}
            </p>
          )}
        </header>

        {memory.isLoading && (
          <div className="flex justify-center py-16">
            <Spinner />
          </div>
        )}
        {memory.error && <ErrorBox error={memory.error} />}

        {data && (
          <>
            {data.pending.length > 0 && (
              <Card className="border-amber-200 dark:border-amber-900">
                <CardHeader
                  title={
                    <span className="flex items-center gap-1.5">
                      <Lightbulb className="h-4 w-4 text-amber-500" aria-hidden />
                      待确认的建议（{data.pending.length}）
                    </span>
                  }
                >
                  <span className="text-xs text-zinc-400">agent 在对话里发现、值得记住的内容</span>
                </CardHeader>
                <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {data.pending.map((c) => (
                    <CandidateRow key={c.id} candidate={c} />
                  ))}
                </ul>
              </Card>
            )}
            <AddMemory index={data} />
            <MemoryList index={data} />
          </>
        )}
      </div>
    </main>
  )
}

import { useMutation, useQuery } from '@tanstack/react-query'
import { Brain, Pencil, Search, Trash2 } from 'lucide-react'
import { useMemo, useState, type KeyboardEvent } from 'react'
import {
  CandidateSection,
  KIND_HINT,
  KIND_SHORT,
  KindBadge,
  MEMORY_FIELD,
  SimilarPrompt,
  useRefreshMemory,
} from '../components/MemoryCandidate'
import { useConfirm, useToast } from '../components/Feedback'
import { ScopeMenu } from '../components/ScopeMenu'
import { formatDay } from '../lib/day'
import { Button, ErrorBox, ListSkeleton, Spinner } from '../components/ui'
import { PageScaffold } from '../components/TopBar'
import { api } from '../lib/api'
import type { MemoryIndex, MemoryItem, MemoryKind, MemorySaveResult } from '../lib/types'

const GLOBAL = '__global__'
const MAX = 300
const KINDS: MemoryKind[] = ['preference', 'term', 'fact']
const ORIGIN_LABEL: Record<string, string> = { explicit: '你手动添加', suggested: '从对话中确认' }
function scopeValue(project: string | null): string {
  return project ?? GLOBAL
}

function isComposing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229
}

function MemoryComposer({ index }: { index: MemoryIndex }) {
  const refresh = useRefreshMemory()
  const toast = useToast()
  const [text, setText] = useState('')
  const [kind, setKind] = useState<MemoryKind>('preference')
  const [scope, setScope] = useState(GLOBAL)
  const [similar, setSimilar] = useState<MemoryItem[] | null>(null)

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
      toast(result.updated ? '已更新这条记忆' : '已记住')
      refresh()
    },
  })

  const submit = () => {
    if (text.trim() && !save.isPending) save.mutate({})
  }

  const chooseKind = (next: MemoryKind) => {
    setKind(next)
    // 表达偏好默认全局；术语、事实挂在项目上，与 CLI 的默认归类一致
    if (next === 'preference') setScope(GLOBAL)
    else if (scope === GLOBAL && index.projects.length === 1) setScope(index.projects[0].key)
  }

  return (
    <section aria-labelledby="mem-new" className="rounded-xl border border-border bg-card">
      <h2 id="mem-new" className="border-b border-border px-4 py-3 text-sm font-semibold text-foreground">
        新增记忆
      </h2>
      <form
        className="space-y-3 p-4"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <div>
          <label htmlFor="mem-text" className="sr-only">
            记忆内容
          </label>
          <textarea
            id="mem-text"
            rows={2}
            maxLength={MAX}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              setSimilar(null)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !isComposing(e)) {
                e.preventDefault()
                submit()
              }
            }}
            placeholder="例如：报告先写结论再写证据；“网关”指 account-gateway"
            className={`${MEMORY_FIELD} resize-none`}
          />
          <p
            className={`mt-1 text-right text-xs tabular-nums ${text.length > MAX - 30 ? 'text-amber-700 dark:text-amber-300' : 'text-muted-foreground'}`}
          >
            {text.length} / {MAX}
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <fieldset>
            <legend className="mb-1.5 text-xs text-muted-foreground">类型</legend>
            <div className="inline-flex rounded-lg bg-muted p-0.5">
              {KINDS.map((k) => (
                <label
                  key={k}
                  title={KIND_HINT[k]}
                  className={`cursor-pointer rounded-md px-3 py-1 text-[13px] transition-colors focus-within:ring-2 focus-within:ring-ring/40 ${
                    kind === k ? 'bg-card font-medium text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <input type="radio" name="mem-kind" value={k} checked={kind === k} onChange={() => chooseKind(k)} className="sr-only" />
                  {KIND_SHORT[k]}
                </label>
              ))}
            </div>
          </fieldset>

          <div>
            <p className="mb-1.5 text-xs text-muted-foreground">作用范围</p>
            <ScopeMenu
              label="作用范围"
              value={scope}
              onChange={setScope}
              options={[{ value: GLOBAL, label: '全局' }, ...index.projects.map((p) => ({ value: p.key, label: p.label, title: p.key }))]}
            />
          </div>

          <div className="ml-auto flex items-center gap-2">
            <span className="hidden text-xs text-muted-foreground sm:inline">⌘ + Enter</span>
            <Button type="submit" variant="primary" disabled={!text.trim() || save.isPending}>
              {save.isPending && <Spinner className="size-3" />}
              保存
            </Button>
          </div>
        </div>

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
      </form>
    </section>
  )
}

function MemoryRow({ memory }: { memory: MemoryItem }) {
  const refresh = useRefreshMemory()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(memory.text)
  const confirm = useConfirm()
  const toast = useToast()
  const update = useMutation({
    mutationFn: () => api.updateMemory(memory.id, text.trim()),
    onSuccess: () => {
      setEditing(false)
      refresh()
      toast('已更新这条记忆')
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteMemory(memory.id),
    onSuccess: () => {
      refresh()
      toast('已删除这条记忆')
    },
  })

  const commit = () => {
    if (text.trim() && text.trim() !== memory.text) update.mutate()
    else setEditing(false)
  }
  const cancel = () => {
    setText(memory.text)
    setEditing(false)
  }

  return (
    <li className="flex gap-3 px-4 py-3">
      <KindBadge kind={memory.kind} />
      <div className="min-w-0 flex-1">
        {editing ? (
          <div className="space-y-2">
            <label htmlFor={`edit-${memory.id}`} className="sr-only">
              编辑记忆
            </label>
            <textarea
              id={`edit-${memory.id}`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !isComposing(e)) commit()
                if (e.key === 'Escape') cancel()
              }}
              rows={2}
              maxLength={MAX}
              autoFocus
              className={`${MEMORY_FIELD} resize-none`}
            />
            <div className="flex gap-2">
              <Button variant="primary" size="xs" disabled={update.isPending || !text.trim()} onClick={commit}>
                保存
              </Button>
              <Button variant="ghost" size="xs" onClick={cancel}>
                取消
              </Button>
            </div>
          </div>
        ) : (
          <>
            <p className="whitespace-pre-wrap break-words text-sm leading-6 text-foreground">{memory.text}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              <span title={memory.project ?? '对所有项目生效'}>{memory.scope_label}</span> · {ORIGIN_LABEL[memory.origin] ?? memory.origin}{' '}
              · <time dateTime={memory.updated_at}>{formatDay(memory.updated_at)}</time>
            </p>
          </>
        )}
        {(update.error || remove.error) && (
          <div className="mt-2">
            <ErrorBox error={update.error ?? remove.error} />
          </div>
        )}
      </div>
      {!editing && (
        <div className="flex shrink-0 items-start gap-0.5">
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-muted-foreground"
            onClick={() => setEditing(true)}
            aria-label="编辑这条记忆"
          >
            <Pencil className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-muted-foreground hover:text-red-600"
            disabled={remove.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: '删除这条记忆？',
                description: `「${memory.text}」删除后，之后的分析不会再参考它。`,
                confirmLabel: '删除',
                danger: true,
              })
              if (ok) remove.mutate()
            }}
            aria-label="删除这条记忆"
          >
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      )}
    </li>
  )
}

function MemoryList({ index }: { index: MemoryIndex }) {
  const [kind, setKind] = useState<MemoryKind | 'all'>('all')
  const [scope, setScope] = useState('all')
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
        (kind === 'all' || m.kind === kind) &&
        (scope === 'all' || scopeValue(m.project) === scope) &&
        (!needle || m.text.toLowerCase().includes(needle)),
    )
  }, [index.memories, kind, scope, q])
  const counts = useMemo(() => {
    const result: Record<string, number> = { all: index.memories.length }
    for (const m of index.memories) result[m.kind] = (result[m.kind] ?? 0) + 1
    return result
  }, [index.memories])
  const tabs: { id: MemoryKind | 'all'; label: string }[] = [
    { id: 'all', label: '全部' },
    ...KINDS.map((k) => ({ id: k, label: KIND_SHORT[k] })),
  ]

  const clearFilters = () => {
    setKind('all')
    setScope('all')
    setQ('')
  }

  return (
    <section aria-labelledby="mem-saved" className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
        <h2 id="mem-saved" className="text-sm font-semibold text-foreground">
          已保存的记忆 <span className="font-normal text-muted-foreground">({index.memories.length})</span>
        </h2>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="relative">
            <span className="sr-only">搜索记忆</span>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索"
              className="h-8 w-40 rounded-lg border border-input bg-background pl-8 pr-2 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
            />
          </label>
          <ScopeMenu
            label="按范围筛选"
            prefix="范围"
            align="end"
            value={scope}
            onChange={setScope}
            options={[{ value: 'all', label: '全部' }, ...scopes.map(([value, label]) => ({ value, label }))]}
          />
        </div>
      </div>

      <div role="group" aria-label="按类型筛选" className="flex gap-1 overflow-x-auto border-b border-border px-3 py-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-pressed={kind === t.id}
            onClick={() => setKind(t.id)}
            className={`inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-3 text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 ${
              kind === t.id ? 'bg-foreground font-medium text-background' : 'text-muted-foreground hover:bg-accent hover:text-foreground'
            }`}
          >
            {t.label}
            <span className="text-xs tabular-nums opacity-70">{counts[t.id] ?? 0}</span>
          </button>
        ))}
      </div>

      {index.memories.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
          <Brain className="size-6 text-muted-foreground" aria-hidden />
          <p className="text-sm font-medium text-foreground">还没有记忆</p>
          <p className="text-[13px] text-muted-foreground">在上方添加，或在对话里说“记住……”。</p>
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
          <p className="text-sm font-medium text-foreground">没有符合条件的记忆</p>
          <Button size="sm" onClick={clearFilters}>
            清除筛选
          </Button>
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((m) => (
            <MemoryRow key={`${m.id}-${m.updated_at}`} memory={m} />
          ))}
        </ul>
      )}
    </section>
  )
}

export function MemoryPage() {
  const memory = useQuery({ queryKey: ['memory'], queryFn: api.memory })
  const data = memory.data

  return (
    <PageScaffold
      title="记忆"
      width="md"
      description="agent 会在每次分析时读取这些记忆：你的偏好、团队术语和项目里的固定信息。记忆越准，回答越贴合你的环境。"
    >
      {memory.isLoading && <ListSkeleton rows={5} label="加载记忆" />}
      {memory.error && <ErrorBox error={memory.error} />}

      {data && (
        <>
          <CandidateSection candidates={data.pending} />
          <MemoryComposer index={data} />
          <MemoryList index={data} />
          <p className="truncate font-mono text-xs text-muted-foreground" title={data.path}>
            存储位置：{data.path}
          </p>
        </>
      )}
    </PageScaffold>
  )
}

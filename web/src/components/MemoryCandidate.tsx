import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, Lightbulb, Pencil, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import type { Tone } from '../lib/format'
import type { MemoryCandidate, MemoryItem, MemoryKind } from '../lib/types'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './Menu'
import { Button, ErrorBox } from './ui'

export const MEMORY_FIELD =
  'w-full rounded-lg border border-input bg-background px-3 py-2 text-sm leading-6 outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring/40'
export const KIND_TONE: Record<MemoryKind, Tone> = { preference: 'blue', term: 'green', fact: 'amber' }

export const KIND_SHORT: Record<MemoryKind, string> = { preference: '偏好', term: '术语', fact: '事实' }
export const KIND_HINT: Record<MemoryKind, string> = {
  preference: '你希望 agent 怎么做',
  term: '团队里的叫法和缩写',
  fact: '项目和环境的固定信息',
}

const KIND_BADGE: Record<MemoryKind, string> = {
  preference: 'bg-brand-soft text-brand-strong',
  term: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300',
  fact: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300',
}

export function KindBadge({ kind }: { kind: MemoryKind }) {
  return (
    <span
      className={`mt-0.5 inline-flex h-5 shrink-0 items-center self-start whitespace-nowrap rounded px-1.5 text-xs font-medium leading-none ${KIND_BADGE[kind]}`}
    >
      {KIND_SHORT[kind]}
    </span>
  )
}

/** Memory 页面与对话页的查询都挂在 ['memory'] 下，失效这个前缀两边会一起刷新 */
export function useRefreshMemory() {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: ['memory'] })
}

/** 相似记忆冲突：后端不保存，列出相似项，由用户选「替换某条」或「仍然新增」 */
export function SimilarPrompt({
  similar,
  busy,
  onReplace,
  onForce,
  onCancel,
}: {
  similar: MemoryItem[]
  busy: boolean
  onReplace: (id: number) => void
  onForce: () => void
  onCancel: () => void
}) {
  return (
    <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
      <p className="flex items-center gap-1.5 text-[13px] font-medium text-amber-900 dark:text-amber-200">
        <TriangleAlert className="size-4" aria-hidden />
        已有相似的记忆
      </p>
      <ul className="mt-1.5 space-y-1.5">
        {similar.map((m) => (
          <li key={m.id} className="flex items-start gap-2 rounded-md bg-card px-2.5 py-1.5 text-[13px]">
            <span className="min-w-0 flex-1 text-foreground">{m.text}</span>
            <Button variant="primary" size="xs" className="shrink-0" disabled={busy} onClick={() => onReplace(m.id)}>
              用新内容替换
            </Button>
          </li>
        ))}
      </ul>
      <div className="mt-2.5 flex flex-wrap gap-2">
        <Button size="xs" disabled={busy} onClick={onForce}>
          两条都保留
        </Button>
        <Button variant="ghost" size="xs" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  )
}

/** 一条待确认的记忆候选：记住 / 编辑后记住 / 忽略（90 天或永久） */
export function CandidateRow({ candidate, className = '' }: { candidate: MemoryCandidate; className?: string }) {
  const refresh = useRefreshMemory()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(candidate.text)
  const [similar, setSimilar] = useState<MemoryItem[] | null>(null)
  const edited = editing && text.trim() !== candidate.text ? text.trim() : undefined

  const accept = useMutation({
    mutationFn: (extra: { replace_id?: number; force?: boolean }) => api.acceptCandidate(candidate.id, { text: edited, ...extra }),
    onSuccess: (result) => {
      if (!result.saved) setSimilar(result.similar)
      else refresh()
    },
  })
  const reject = useMutation({
    mutationFn: (permanent: boolean) => api.rejectCandidate(candidate.id, permanent),
    onSuccess: refresh,
  })
  const busy = accept.isPending || reject.isPending

  return (
    <li className={`@container rounded-lg border border-border bg-card p-3 ${className}`}>
      <div className="flex flex-col gap-3 @lg:flex-row @lg:items-center">
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <KindBadge kind={candidate.kind} />
            {editing ? (
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={2}
                maxLength={300}
                autoFocus
                aria-label="修改建议内容"
                className={MEMORY_FIELD}
              />
            ) : (
              <p className="text-sm leading-6 text-foreground">{candidate.text}</p>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {candidate.reason} · 作用于 {candidate.scope_label}
          </p>
        </div>
        {!similar && (
          <div className="flex shrink-0 items-center gap-1.5">
            <Button variant="primary" size="sm" disabled={busy || (editing && !text.trim())} onClick={() => accept.mutate({})}>
              <Check className="size-3.5" aria-hidden />
              {editing ? '保存并记住' : '记住'}
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              className="text-muted-foreground"
              disabled={busy}
              onClick={() => {
                setText(candidate.text)
                setEditing((v) => !v)
              }}
              aria-label={editing ? '取消编辑' : '编辑后再记住'}
              title={editing ? '取消编辑' : '编辑后再记住'}
            >
              <Pencil className="size-3.5" aria-hidden />
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                disabled={busy}
                className="inline-flex h-8 items-center gap-1 rounded-md border border-border bg-background px-2.5 text-[13px] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50"
              >
                忽略
                <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuItem onSelect={() => reject.mutate(false)}>90 天内不再提示</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => reject.mutate(true)}>永不提示</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </div>
      {similar && (
        <div className="mt-3">
          <SimilarPrompt
            similar={similar}
            busy={busy}
            onReplace={(id) => accept.mutate({ replace_id: id })}
            onForce={() => accept.mutate({ force: true })}
            onCancel={() => setSimilar(null)}
          />
        </div>
      )}
      {(accept.error || reject.error) && (
        <div className="mt-2">
          <ErrorBox error={accept.error ?? reject.error} />
        </div>
      )}
    </li>
  )
}

/** 待确认建议的外框：浅品牌色底，内部每条是白卡片 */
export function CandidateSection({ candidates }: { candidates: MemoryCandidate[] }) {
  if (!candidates.length) return null
  return (
    <section aria-labelledby="mem-candidates" className="rounded-xl border border-primary/25 bg-brand-soft/40">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 pt-3.5">
        <Lightbulb className="size-4 text-brand-strong" aria-hidden />
        <h2 id="mem-candidates" className="text-sm font-semibold text-foreground">
          待确认的建议
        </h2>
        <span className="text-xs text-muted-foreground">agent 从最近的对话里发现，确认后才会保存</span>
      </div>
      <ul className="space-y-2 p-3">
        {candidates.map((c) => (
          <CandidateRow key={c.id} candidate={c} />
        ))}
      </ul>
    </section>
  )
}

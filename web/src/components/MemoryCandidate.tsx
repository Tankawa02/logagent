import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Check, Pencil, X } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import type { Tone } from '../lib/format'
import type { MemoryCandidate, MemoryItem, MemoryKind } from '../lib/types'
import { Badge, Button, ErrorBox } from './ui'

export const MEMORY_FIELD =
  'w-full rounded-md border border-zinc-200 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-800 dark:bg-zinc-950'
export const KIND_TONE: Record<MemoryKind, Tone> = { preference: 'blue', term: 'green', fact: 'amber' }

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
    <div role="alert" className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950/40">
      <p className="font-medium text-amber-800 dark:text-amber-300">已有相似记忆，替换它还是另存一条？</p>
      <ul className="space-y-1.5">
        {similar.map((m) => (
          <li key={m.id} className="flex items-start gap-2 rounded bg-white/70 px-2 py-1.5 dark:bg-zinc-900/60">
            <span className="font-mono text-xs text-zinc-400">#{m.id}</span>
            <span className="min-w-0 flex-1 text-zinc-700 dark:text-zinc-200">{m.text}</span>
            <Button variant="secondary" className="shrink-0 px-2 py-0.5 text-xs" disabled={busy} onClick={() => onReplace(m.id)}>
              替换这条
            </Button>
          </li>
        ))}
      </ul>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" className="text-xs" onClick={onCancel}>
          取消
        </Button>
        <Button variant="secondary" className="text-xs" disabled={busy} onClick={onForce}>
          仍然新增
        </Button>
      </div>
    </div>
  )
}

/** 一条待确认的记忆候选：记住 / 编辑后记住 / 不保存 / 不再提示 */
export function CandidateRow({ candidate, className = 'px-4 py-3' }: { candidate: MemoryCandidate; className?: string }) {
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
    <li className={`space-y-2 ${className}`}>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Badge tone={KIND_TONE[candidate.kind]}>{candidate.kind_label}</Badge>
        <Badge>{candidate.scope_label}</Badge>
        <span className="text-zinc-400">{candidate.reason}</span>
      </div>
      {editing ? (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          maxLength={300}
          aria-label="修改建议内容"
          className={MEMORY_FIELD}
        />
      ) : (
        <p className="text-sm text-zinc-800 dark:text-zinc-100">{candidate.text}</p>
      )}
      {similar ? (
        <SimilarPrompt
          similar={similar}
          busy={busy}
          onReplace={(id) => accept.mutate({ replace_id: id })}
          onForce={() => accept.mutate({ force: true })}
          onCancel={() => setSimilar(null)}
        />
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            className="px-2 py-1 text-xs"
            disabled={busy || (editing && !text.trim())}
            onClick={() => accept.mutate({})}
          >
            <Check className="h-3.5 w-3.5" aria-hidden />
            {editing ? '保存修改后的' : '记住'}
          </Button>
          <Button className="px-2 py-1 text-xs" disabled={busy} onClick={() => setEditing((v) => !v)}>
            <Pencil className="h-3.5 w-3.5" aria-hidden />
            {editing ? '取消编辑' : '编辑'}
          </Button>
          <Button variant="ghost" className="px-2 py-1 text-xs" disabled={busy} onClick={() => reject.mutate(false)} title="90 天内不再提示">
            <X className="h-3.5 w-3.5" aria-hidden />
            不保存
          </Button>
          <Button variant="ghost" className="px-2 py-1 text-xs" disabled={busy} onClick={() => reject.mutate(true)} title="以后都不再提示相似内容">
            <Ban className="h-3.5 w-3.5" aria-hidden />
            不再提示
          </Button>
        </div>
      )}
      {(accept.error || reject.error) && <ErrorBox error={accept.error ?? reject.error} />}
    </li>
  )
}

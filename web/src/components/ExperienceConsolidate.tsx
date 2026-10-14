import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Combine, GitMerge, X } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import { shortModel } from '../lib/format'
import type { ConsolidationProposal, ConsolidationResult, ExperienceIndex } from '../lib/types'
import { ExperienceDraftFields, type ExperienceDraft } from './ExperienceDraftFields'
import { useToast } from './Feedback'
import { Select } from './Select'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Spinner } from './ui'

const GLOBAL = '__global__'
const STATUS_TONE = { candidate: 'amber', active: 'green', retired: 'gray', promoted: 'blue' } as const
const STATUS_LABEL = { candidate: '候选', active: '生效', retired: '已淘汰', promoted: '已提升为 skill' } as const

type ScopeOption = { value: string; label: string; count: number }

function scopeOptions(index: ExperienceIndex): ScopeOption[] {
  const live = index.entries.filter((e) => e.status === 'candidate' || e.status === 'active')
  const options: ScopeOption[] = [
    { value: GLOBAL, label: '全局', count: live.filter((e) => e.project === null).length },
    ...index.projects.map((p) => ({ value: p.key, label: p.label, count: live.filter((e) => e.project === p.key).length })),
  ]
  return options.filter((o) => o.count >= 2)
}

function ProposalCard({
  proposal,
  model,
  position,
  onDone,
}: {
  proposal: ConsolidationProposal
  model: string
  position: number
  onDone: () => void
}) {
  const client = useQueryClient()
  const toast = useToast()
  const [draft, setDraft] = useState<ExperienceDraft>({ title: proposal.title, trigger: proposal.trigger, strategy: proposal.strategy })
  const merge = useMutation({
    mutationFn: () =>
      api.mergeExperience({
        keep: proposal.keep,
        ids: proposal.ids,
        title: draft.title.trim(),
        trigger: draft.trigger.trim(),
        strategy: draft.strategy.trim(),
        reason: proposal.reason,
        model,
      }),
    onSuccess: (entry) => {
      void client.invalidateQueries({ queryKey: ['experience'] })
      toast(`已合并为 E${entry.id}，其余 ${proposal.ids.length - 1} 条转为已淘汰`)
      onDone()
    },
  })
  const absorbed = proposal.ids.filter((id) => id !== proposal.keep)
  const ready = draft.title.trim() && draft.strategy.trim()

  return (
    <li className="space-y-3 px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground/70">建议 {position}</span>
        <span className="text-sm text-foreground/80">{proposal.reason}</span>
      </div>
      {proposal.shared_step && (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-muted-foreground">共同的排查动作：</span>
          {proposal.shared_step}
        </p>
      )}
      <ul className="space-y-1.5" aria-label="将被合并的经验">
        {proposal.members.map((member) => (
          <li key={member.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-md border border-border px-2.5 py-2 text-xs">
            <span className="font-mono text-muted-foreground/70">E{member.id}</span>
            <Badge tone={STATUS_TONE[member.status]}>{STATUS_LABEL[member.status]}</Badge>
            {member.id === proposal.keep && <Badge tone="blue">保留</Badge>}
            <span className="min-w-0 flex-1 break-words font-medium text-foreground">{member.title}</span>
            <span className="text-muted-foreground/70">
              有用 {member.helpful} · 有害 {member.harmful}
            </span>
            <p className="basis-full break-words text-muted-foreground">
              适用：{member.trigger || '通用'}｜{member.strategy}
            </p>
          </li>
        ))}
      </ul>
      <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-3 dark:border-brand-900 dark:bg-brand-950/20">
        <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-brand-700 dark:text-brand-300">
          <GitMerge className="h-3.5 w-3.5" aria-hidden />
          合并后（可修改）
        </p>
        <ExperienceDraftFields draft={draft} onChange={setDraft} idPrefix={`consolidate-${proposal.keep}`} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" size="xs" disabled={!ready || merge.isPending} onClick={() => merge.mutate()}>
          {merge.isPending ? <Spinner className="h-3 w-3" /> : <GitMerge className="h-3.5 w-3.5" aria-hidden />}
          合并
        </Button>
        <Button size="xs" disabled={merge.isPending} onClick={onDone}>
          <X className="h-3.5 w-3.5" aria-hidden />
          忽略
        </Button>
        <span className="text-xs text-muted-foreground/70">
          保留 E{proposal.keep}（{proposal.status === 'active' ? '生效' : '候选'}），票数累加；
          {absorbed.map((id) => `E${id}`).join('、')} 转为已淘汰，来历里可追溯。
        </span>
      </div>
      {merge.error && <ErrorBox error={merge.error} />}
    </li>
  )
}

export function ExperienceConsolidate({ index }: { index: ExperienceIndex }) {
  const options = scopeOptions(index)
  const [scope, setScope] = useState(options[0]?.value ?? GLOBAL)
  const [result, setResult] = useState<ConsolidationResult | null>(null)
  const [dismissed, setDismissed] = useState<Set<number>>(new Set())
  const analyze = useMutation({
    mutationFn: (target: string) => api.consolidateExperience(target === GLOBAL ? null : target),
    onSuccess: (data) => {
      setResult(data)
      setDismissed(new Set())
    },
  })

  if (options.length === 0) return null
  const selected = options.some((o) => o.value === scope) ? scope : options[0].value
  const pending = result?.proposals.filter((p) => !dismissed.has(p.keep)) ?? []

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Combine className="h-4 w-4 text-brand-600 dark:text-brand-400" aria-hidden />
            精简经验库
          </span>
        }
      />
      <div className="space-y-3 px-4 py-4">
        <p className="text-pretty text-sm text-muted-foreground">
          让收尾模型找出同一范围里讲同一类故障的经验，起草合并后的内容。只给建议，逐条确认后才会合并。
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1 space-y-1">
            <label htmlFor="consolidate-scope" className="text-xs font-medium text-muted-foreground">
              范围
            </label>
            <Select
              id="consolidate-scope"
              value={selected}
              onChange={(value) => {
                setScope(value)
                setResult(null)
              }}
              options={options.map((o) => ({ value: o.value, label: o.label, hint: `${o.count} 条候选 / 生效`, title: o.value }))}
            />
          </div>
          <Button variant="primary" disabled={analyze.isPending} onClick={() => analyze.mutate(selected)}>
            {analyze.isPending ? <Spinner className="h-3 w-3" /> : <Combine className="h-4 w-4" aria-hidden />}
            {analyze.isPending ? '分析中…' : '找出同类经验'}
          </Button>
        </div>
        {analyze.error && <ErrorBox error={analyze.error} />}
      </div>
      {result && (
        <div className="border-t border-border">
          <p className="px-4 pt-3 text-xs text-muted-foreground/70" aria-live="polite">
            检查了 {result.scanned} 条，建议合并 {result.proposals.length} 组
            {result.model && <span className="font-mono"> · {shortModel(result.model)}</span>}
          </p>
          {pending.length === 0 ? (
            <Empty>{result.proposals.length ? '这一批建议都处理完了。' : '没有发现可以合并的同类经验。'}</Empty>
          ) : (
            <ul className="divide-y divide-border">
              {pending.map((proposal) => (
                <ProposalCard
                  key={proposal.keep}
                  proposal={proposal}
                  model={result.model}
                  position={result.proposals.indexOf(proposal) + 1}
                  onDone={() => setDismissed((prev) => new Set(prev).add(proposal.keep))}
                />
              ))}
            </ul>
          )}
          {result.rejected.length > 0 && (
            <details className="px-4 pb-3 text-xs text-muted-foreground/70">
              <summary className="cursor-pointer select-none">{result.rejected.length} 组建议未通过检查，已丢弃</summary>
              <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
                {result.rejected.map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Card>
  )
}

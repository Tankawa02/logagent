import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Archive, ArchiveRestore, Check, ChevronDown, History, Lightbulb, Pencil, Plus, Trash2, X } from 'lucide-react'
import { useId, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { ExperienceConsolidate } from '../components/ExperienceConsolidate'
import { ExperienceDraftFields as DraftFields, type ExperienceDraft as Draft } from '../components/ExperienceDraftFields'
import { ExperienceHistory } from '../components/ExperienceHistory'
import { useConfirm, useToast } from '../components/Feedback'
import { Select } from '../components/Select'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, ListSkeleton, Spinner } from '../components/ui'
import { PageScaffold } from '../components/TopBar'
import { api } from '../lib/api'
import type { ExperienceIndex, ExperienceItem } from '../lib/types'

const GLOBAL = '__global__'
const ORIGIN_LABEL: Record<string, string> = { eval: '离线评测', online: '在线复盘', manual: '手动添加' }
const STATUS_TONE = { candidate: 'amber', active: 'green', retired: 'gray', promoted: 'blue' } as const
const STATUS_LABEL = { candidate: '候选', active: '生效', retired: '已淘汰', promoted: '已提升为 skill' } as const

function isComposing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229
}

function useRefresh() {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: ['experience'] })
}

function AddExperience({ index }: { index: ExperienceIndex }) {
  const refresh = useRefresh()
  const toast = useToast()
  const empty: Draft = { title: '', trigger: '通用', strategy: '' }
  const [draft, setDraft] = useState<Draft>(empty)
  const [scope, setScope] = useState(GLOBAL)
  const save = useMutation({
    mutationFn: () =>
      api.addExperience({
        title: draft.title.trim(),
        trigger: draft.trigger.trim(),
        strategy: draft.strategy.trim(),
        project: scope === GLOBAL ? null : scope,
      }),
    onSuccess: (result) => {
      setDraft(empty)
      refresh()
      toast(result.created ? `已添加 E${result.entry.id}` : `与已有经验相似，已合并到 E${result.entry.id}`)
    },
  })
  const ready = draft.title.trim() && draft.strategy.trim()

  function submit(event: FormEvent) {
    event.preventDefault()
    if (ready && !save.isPending) save.mutate()
  }

  return (
    <Card>
      <CardHeader title="手动添加经验" />
      <form
        onSubmit={submit}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !isComposing(e)) submit(e)
        }}
        className="space-y-3 px-4 py-4"
      >
        <DraftFields draft={draft} onChange={setDraft} idPrefix="new-experience" />
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1 space-y-1">
            <label htmlFor="experience-scope" className="text-xs font-medium text-muted-foreground">
              范围
            </label>
            <Select
              id="experience-scope"
              value={scope}
              onChange={setScope}
              options={[
                { value: GLOBAL, label: '全局', hint: '所有项目生效' },
                ...index.projects.map((p) => ({ value: p.key, label: p.label, hint: '仅该项目', title: p.key })),
              ]}
            />
          </div>
          <Button type="submit" variant="primary" disabled={!ready || save.isPending}>
            {save.isPending ? <Spinner className="h-3 w-3" /> : <Plus className="h-4 w-4" aria-hidden />}
            添加并生效
          </Button>
        </div>
        <p className="text-xs text-muted-foreground/70">
          内容会脱敏后保存；含链接、文件行号、长 ID、时间等只和单次故障有关的细节会被拒绝。
        </p>
        {save.error && <ErrorBox error={save.error} />}
      </form>
    </Card>
  )
}

function ExperienceRow({ entry }: { entry: ExperienceItem }) {
  const refresh = useRefresh()
  const toast = useToast()
  const confirm = useConfirm()
  const [editing, setEditing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const historyId = useId()
  const [draft, setDraft] = useState<Draft>({ title: entry.title, trigger: entry.trigger, strategy: entry.strategy })

  const status = useMutation({
    mutationFn: (next: 'candidate' | 'active' | 'retired') => api.setExperienceStatus(entry.id, next),
    onSuccess: (item) => {
      refresh()
      toast(item.status === 'active' ? `E${entry.id} 已生效` : item.status === 'retired' ? `E${entry.id} 已淘汰` : '已更新')
    },
  })
  const update = useMutation({
    mutationFn: () =>
      api.updateExperience(entry.id, { title: draft.title.trim(), trigger: draft.trigger.trim(), strategy: draft.strategy.trim() }),
    onSuccess: () => {
      setEditing(false)
      refresh()
      toast(`已更新 E${entry.id}`)
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteExperience(entry.id),
    onSuccess: () => {
      refresh()
      toast(`已删除 E${entry.id}`)
    },
  })
  const busy = status.isPending || update.isPending || remove.isPending
  const error = status.error ?? update.error ?? remove.error

  return (
    <li className="group flex flex-wrap items-start gap-3 px-4 py-3">
      <span className="w-9 shrink-0 pt-0.5 text-right font-mono text-xs text-muted-foreground/70">E{entry.id}</span>
      <div className="min-w-0 flex-1 space-y-1.5">
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (draft.title.trim() && draft.strategy.trim()) update.mutate()
            }}
            className="space-y-2"
          >
            <DraftFields draft={draft} onChange={setDraft} idPrefix={`experience-${entry.id}`} />
            <div className="flex gap-2">
              <Button type="submit" variant="primary" size="xs" disabled={busy}>
                保存
              </Button>
              <Button
                size="xs"
                onClick={() => {
                  setDraft({ title: entry.title, trigger: entry.trigger, strategy: entry.strategy })
                  setEditing(false)
                }}
              >
                取消
              </Button>
            </div>
          </form>
        ) : (
          <>
            <p className="break-words text-sm font-medium text-foreground">{entry.title}</p>
            <p className="break-words text-xs text-muted-foreground">
              <span className="font-medium text-muted-foreground">适用：</span>
              {entry.trigger || '通用'}
            </p>
            <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/80">{entry.strategy}</p>
          </>
        )}
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground/70">
          <Badge tone={STATUS_TONE[entry.status]}>{STATUS_LABEL[entry.status]}</Badge>
          <Badge title={entry.project ?? '对所有项目生效'}>{entry.scope}</Badge>
          {entry.promotable && (
            <Badge tone="blue" title={`可以用 log-agent experience promote ${entry.id} 写成 skill`}>
              可提升为 skill
            </Badge>
          )}
          <span>{ORIGIN_LABEL[entry.origin] ?? entry.origin}</span>
          <span aria-hidden>·</span>
          <span>
            有用 {entry.helpful} · 有害 {entry.harmful}
          </span>
          <span aria-hidden>·</span>
          <span>{entry.updated_at.slice(0, 16)}</span>
          <button
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            aria-expanded={showHistory}
            aria-controls={historyId}
            className="ml-1 inline-flex items-center gap-1 rounded px-1 font-medium text-brand-600 hover:underline dark:text-brand-400"
          >
            <History className="h-3.5 w-3.5" aria-hidden />
            {showHistory ? '收起来历' : '查看来历'}
            <ChevronDown className={`h-3 w-3 transition-transform ${showHistory ? 'rotate-180' : ''}`} aria-hidden />
          </button>
        </div>
        {error && <ErrorBox error={error} />}
      </div>
      {!editing && (
        <div className="flex shrink-0 items-center gap-1">
          {entry.status === 'candidate' ? (
            <>
              <Button variant="primary" size="xs" disabled={busy} onClick={() => status.mutate('active')}>
                <Check className="h-3.5 w-3.5" aria-hidden />
                生效
              </Button>
              <Button size="xs" disabled={busy} onClick={() => remove.mutate()}>
                <X className="h-3.5 w-3.5" aria-hidden />
                丢弃
              </Button>
            </>
          ) : (
            <div className="flex gap-0.5 opacity-0 focus-within:opacity-100 group-hover:opacity-100">
              {entry.status === 'active' ? (
                <IconButton label={`淘汰 E${entry.id}`} disabled={busy} onClick={() => status.mutate('retired')}>
                  <Archive className="h-3.5 w-3.5" />
                </IconButton>
              ) : (
                entry.status === 'retired' && (
                  <IconButton label={`重新启用 E${entry.id}`} disabled={busy} onClick={() => status.mutate('active')}>
                    <ArchiveRestore className="h-3.5 w-3.5" />
                  </IconButton>
                )
              )}
              <IconButton label={`���辑 E${entry.id}`} disabled={busy} onClick={() => setEditing(true)}>
                <Pencil className="h-3.5 w-3.5" />
              </IconButton>
              <IconButton
                label={`删除 E${entry.id}`}
                disabled={busy}
                danger
                onClick={async () => {
                  const ok = await confirm({
                    title: `删除经验 E${entry.id}？`,
                    description: '彻底删除，之后的排查不再参考它。只想暂停使用可以选择淘汰。',
                    confirmLabel: '删除',
                    danger: true,
                  })
                  if (ok) remove.mutate()
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </IconButton>
            </div>
          )}
        </div>
      )}
      {showHistory && (
        <div id={historyId} className="min-w-0 basis-full border-t border-border pt-3 sm:pl-12">
          <ExperienceHistory entry={entry} />
        </div>
      )}
    </li>
  )
}

function IconButton({
  label,
  onClick,
  disabled,
  danger = false,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`rounded p-1.5 text-muted-foreground/70 hover:bg-accent ${danger ? 'hover:text-red-600' : 'hover:text-foreground'}`}
    >
      {children}
    </button>
  )
}

function ReflectionStatus({ index }: { index: ExperienceIndex }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm">
      <span className="flex items-center gap-1.5 text-foreground/80">
        <span className={`h-2 w-2 rounded-full ${index.reflection ? 'bg-emerald-500' : 'bg-muted-foreground/30'}`} aria-hidden />
        在线复盘{index.reflection ? '已开启' : '已关闭'}
      </span>
      <span className="text-xs text-muted-foreground">
        {index.reflection ? '每轮结束后复盘一次，新经验先进候选，由你确认后生效。' : '只使用离线评测学到的和手动添加的经验。'}
      </span>
      <Link to="/settings" className="ml-auto text-xs font-medium text-brand-600 hover:underline dark:text-brand-400">
        在设置里切换
      </Link>
      {!index.enabled && (
        <p className="w-full text-xs text-amber-700 dark:text-amber-400">
          经验注入已被配置 experience = false 关闭，下面的经验暂时不会被使用。
        </p>
      )}
    </div>
  )
}

export function ExperiencePage() {
  const experience = useQuery({ queryKey: ['experience'], queryFn: api.experience })
  const [showArchived, setShowArchived] = useState(false)
  const data = experience.data
  const candidates = data?.entries.filter((e) => e.status === 'candidate') ?? []
  const active = data?.entries.filter((e) => e.status === 'active') ?? []
  const archived = data?.entries.filter((e) => e.status === 'retired' || e.status === 'promoted') ?? []

  return (
    <PageScaffold
      title="经验"
      width="md"
      description={
        <>
          从过往排查里提炼的可复用策略。生效的经验会按与问题的相关度挑选几条，作为排查思路提示给 agent；被标为有害的会自动淘汰。
          {data && (
            <span className="mt-1 block truncate font-mono text-xs text-muted-foreground/80" title={data.path}>
              {data.path}
            </span>
          )}
        </>
      }
    >
      {experience.isLoading && <ListSkeleton rows={5} label="加载经验" />}
      {experience.error && <ErrorBox error={experience.error} />}

      {data && (
        <>
          <ReflectionStatus index={data} />

          {candidates.length > 0 && (
            <Card className="border-amber-200 dark:border-amber-900">
              <CardHeader
                title={
                  <span className="flex items-center gap-1.5">
                    <Lightbulb className="h-4 w-4 text-amber-500" aria-hidden />
                    待确认的候选（{candidates.length}）
                  </span>
                }
              />
              <ul className="divide-y divide-border">
                {candidates.map((entry) => (
                  <ExperienceRow key={entry.id} entry={entry} />
                ))}
              </ul>
            </Card>
          )}

          <Card>
            <CardHeader title={`生效中（${active.length}）`}>
              {archived.length > 0 && (
                <Button variant="ghost" size="xs" onClick={() => setShowArchived((v) => !v)}>
                  {showArchived ? '收起已淘汰' : `查看已淘汰 / 已提升（${archived.length}）`}
                </Button>
              )}
            </CardHeader>
            {active.length === 0 ? (
              <Empty>
                还没有生效的经验。可以运行 <code className="font-mono">log-agent eval learn</code>{' '}
                从评测案例里学习，或在设置里开启在线复盘。
              </Empty>
            ) : (
              <ul className="divide-y divide-border">
                {active.map((entry) => (
                  <ExperienceRow key={entry.id} entry={entry} />
                ))}
              </ul>
            )}
            {showArchived && archived.length > 0 && (
              <ul className="divide-y divide-border border-t border-border bg-muted/50">
                {archived.map((entry) => (
                  <ExperienceRow key={entry.id} entry={entry} />
                ))}
              </ul>
            )}
          </Card>

          <ExperienceConsolidate index={data} />

          <AddExperience index={data} />
        </>
      )}
    </PageScaffold>
  )
}

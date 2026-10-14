import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Archive,
  Check,
  ChevronDown,
  History,
  Lightbulb,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useId, useState, type KeyboardEvent } from 'react'
import { ExperienceConsolidate } from '../components/ExperienceConsolidate'
import { ExperienceDraftFields as DraftFields, type ExperienceDraft as Draft } from '../components/ExperienceDraftFields'
import { ExperienceHistory } from '../components/ExperienceHistory'
import { useConfirm, useToast } from '../components/Feedback'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../components/Menu'
import { ScopeMenu } from '../components/ScopeMenu'
import { Button, ErrorBox, ListSkeleton, Spinner } from '../components/ui'
import { PageScaffold } from '../components/TopBar'
import { api } from '../lib/api'
import { formatDay } from '../lib/day'
import type { ExperienceIndex, ExperienceItem } from '../lib/types'

const GLOBAL = '__global__'
const ORIGIN_LABEL: Record<string, string> = { eval: '离线评测', online: '在线复盘', manual: '手动添加' }

function isComposing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229
}

function useRefresh() {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: ['experience'] })
}

function AddExperience({ index, onClose }: { index: ExperienceIndex; onClose: () => void }) {
  const refresh = useRefresh()
  const toast = useToast()
  const [draft, setDraft] = useState<Draft>({ title: '', trigger: '', strategy: '' })
  const [scope, setScope] = useState(GLOBAL)
  const save = useMutation({
    mutationFn: () =>
      api.addExperience({
        title: draft.title.trim(),
        trigger: draft.trigger.trim() || '通用',
        strategy: draft.strategy.trim(),
        project: scope === GLOBAL ? null : scope,
      }),
    onSuccess: (result) => {
      refresh()
      toast(result.created ? '已添加并生效' : '与已有经验相似，已合并')
      onClose()
    },
  })
  const ready = Boolean(draft.title.trim() && draft.strategy.trim())
  const submit = () => {
    if (ready && !save.isPending) save.mutate()
  }

  return (
    <section aria-labelledby="exp-new" className="rounded-xl border border-border bg-card p-4">
      <h2 id="exp-new" className="mb-3 text-sm font-semibold text-foreground">
        手动添加经验
      </h2>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !isComposing(e)) {
            e.preventDefault()
            submit()
          }
        }}
      >
        <DraftFields draft={draft} onChange={setDraft} idPrefix="new-experience" />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] text-muted-foreground">作用范围</span>
          <ScopeMenu
            label="作用范围"
            value={scope}
            onChange={setScope}
            options={[{ value: GLOBAL, label: '全局' }, ...index.projects.map((p) => ({ value: p.key, label: p.label, title: p.key }))]}
          />
          <div className="ml-auto flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" variant="primary" disabled={!ready || save.isPending}>
              {save.isPending && <Spinner className="size-3" />}
              添加并生效
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">内容会脱敏后保存；链接、文件行号、长 ID 等只和单次故障有关的细节会被拒绝。</p>
        {save.error && <ErrorBox error={save.error} />}
      </form>
    </section>
  )
}

function StatusPill({ entry }: { entry: ExperienceItem }) {
  if (entry.status === 'promoted')
    return <span className="rounded-full bg-brand-soft px-2 py-0.5 text-xs font-medium text-brand-strong">已升级为技能</span>
  if (entry.status === 'retired') return <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">已归档</span>
  if (entry.status === 'active' && entry.promotable)
    return (
      <span
        className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
        title={`可以用 log-agent experience promote ${entry.id} 写成技能`}
      >
        可升级为技能
      </span>
    )
  return null
}

function ExperienceCard({ entry }: { entry: ExperienceItem }) {
  const refresh = useRefresh()
  const toast = useToast()
  const confirm = useConfirm()
  const [editing, setEditing] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const historyId = useId()
  const [draft, setDraft] = useState<Draft>({ title: entry.title, trigger: entry.trigger, strategy: entry.strategy })
  const archived = entry.status === 'retired' || entry.status === 'promoted'

  const status = useMutation({
    mutationFn: (next: 'candidate' | 'active' | 'retired') => api.setExperienceStatus(entry.id, next),
    onSuccess: (item) => {
      refresh()
      toast(item.status === 'active' ? '已生效' : item.status === 'retired' ? '已归档' : '已更新')
    },
  })
  const update = useMutation({
    mutationFn: () =>
      api.updateExperience(entry.id, { title: draft.title.trim(), trigger: draft.trigger.trim(), strategy: draft.strategy.trim() }),
    onSuccess: () => {
      setEditing(false)
      refresh()
      toast('已保存修改')
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteExperience(entry.id),
    onSuccess: () => {
      refresh()
      toast(entry.status === 'candidate' ? '已丢弃' : '已删除')
    },
  })
  const busy = status.isPending || update.isPending || remove.isPending
  const error = status.error ?? update.error ?? remove.error

  const askDelete = async () => {
    const ok = await confirm({
      title: '删除这条经验？',
      description: `「${entry.title}」及其投票记录会被永久删除。如果只是暂时不想用，可以选择归档。`,
      confirmLabel: '删除',
      danger: true,
    })
    if (ok) remove.mutate()
  }

  if (editing) {
    const cancel = () => {
      setDraft({ title: entry.title, trigger: entry.trigger, strategy: entry.strategy })
      setEditing(false)
    }
    return (
      <li className="rounded-xl border border-border bg-card p-4">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (draft.title.trim() && draft.strategy.trim()) update.mutate()
          }}
        >
          <DraftFields draft={draft} onChange={setDraft} idPrefix={`experience-${entry.id}`} />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={cancel}>
              取消
            </Button>
            <Button type="submit" variant="primary" disabled={busy || !draft.title.trim() || !draft.strategy.trim()}>
              保存
            </Button>
          </div>
          {update.error && <ErrorBox error={update.error} />}
        </form>
      </li>
    )
  }

  return (
    <li className={`rounded-xl border border-border p-4 ${archived ? 'bg-muted/30' : 'bg-card'}`}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className={`break-words text-[15px] font-medium leading-6 ${archived ? 'text-muted-foreground' : 'text-foreground'}`}>
              {entry.title}
            </h3>
            <StatusPill entry={entry} />
          </div>
          <p className="mt-1 break-words text-[13px] text-muted-foreground">
            <span className="font-medium text-foreground/80">适用：</span>
            {entry.trigger || '通用'}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {entry.status === 'candidate' && (
            <>
              <Button variant="primary" disabled={busy} onClick={() => status.mutate('active')}>
                <Check className="size-3.5" aria-hidden />
                生效
              </Button>
              <Button variant="ghost" className="text-muted-foreground" disabled={busy} onClick={() => remove.mutate()}>
                <X className="size-3.5" aria-hidden />
                丢弃
              </Button>
            </>
          )}
          {entry.status === 'retired' && (
            <Button disabled={busy} onClick={() => status.mutate('active')}>
              <RotateCcw className="size-3.5" aria-hidden />
              恢复
            </Button>
          )}
          {entry.status !== 'candidate' && (
            <DropdownMenu>
              <DropdownMenuTrigger
                disabled={busy}
                aria-label={`${entry.title} 的更多操作`}
                className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
              >
                <MoreHorizontal className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuItem onSelect={() => setEditing(true)}>
                  <Pencil />
                  编辑
                </DropdownMenuItem>
                {entry.status === 'active' && (
                  <DropdownMenuItem onSelect={() => status.mutate('retired')}>
                    <Archive />
                    归档
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onSelect={() => void askDelete()}>
                  <Trash2 />
                  删除
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      <p className={`mt-3 whitespace-pre-wrap break-words text-sm leading-6 ${archived ? 'text-muted-foreground' : 'text-foreground/90'}`}>
        {entry.strategy}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3 text-xs text-muted-foreground">
        <span className="rounded bg-muted px-1.5 py-0.5 text-foreground/80" title={entry.project ?? '对所有项目生效'}>
          {entry.scope}
        </span>
        <span>{ORIGIN_LABEL[entry.origin] ?? entry.origin}</span>
        <span className="inline-flex items-center gap-1" title="被采纳后判定为有用的次数">
          <ThumbsUp className="size-3.5" aria-hidden />
          <span className="sr-only">有用</span>
          {entry.helpful}
        </span>
        <span
          className={`inline-flex items-center gap-1 ${entry.harmful > entry.helpful ? 'text-red-600 dark:text-red-400' : ''}`}
          title="被采纳后判定为误导的次数"
        >
          <ThumbsDown className="size-3.5" aria-hidden />
          <span className="sr-only">误导</span>
          {entry.harmful}
        </span>
        <span>
          更新于 <time dateTime={entry.updated_at}>{formatDay(entry.updated_at)}</time>
        </span>
        <button
          type="button"
          aria-expanded={historyOpen}
          aria-controls={historyId}
          onClick={() => setHistoryOpen((o) => !o)}
          className="ml-auto inline-flex items-center gap-1 rounded-sm outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
        >
          <History className="size-3.5" aria-hidden />
          来历
          <ChevronDown className={`size-3.5 transition-transform ${historyOpen ? 'rotate-180' : ''}`} aria-hidden />
        </button>
      </div>

      {historyOpen && (
        <div id={historyId} className="mt-3">
          <ExperienceHistory entry={entry} />
        </div>
      )}
      {error && !update.error && (
        <div className="mt-3">
          <ErrorBox error={error} />
        </div>
      )}
    </li>
  )
}

function ReflectionStatus({ index }: { index: ExperienceIndex }) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <div className="flex items-center gap-3">
        <span
          className={`flex size-8 shrink-0 items-center justify-center rounded-lg ${
            index.reflection
              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
              : 'bg-muted text-muted-foreground'
          }`}
        >
          <RefreshCw className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">在线复盘{index.reflection ? '已开启' : '已关闭'}</p>
          <p className="text-xs text-muted-foreground">
            {index.reflection ? '每次分析结束后，agent 会总结新经验并给已有经验投票' : '开启后，agent 会在分析结束后自动总结经验'}
          </p>
        </div>
        <Link
          to="/settings"
          className="shrink-0 rounded-sm text-[13px] text-brand-strong underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/40"
        >
          去设置
        </Link>
      </div>
      {!index.enabled && (
        <p className="mt-3 flex items-center gap-1.5 border-t border-border pt-3 text-xs text-amber-700 dark:text-amber-300">
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
          经验注入已被配置 experience = false 关闭，下面的经验暂时不会被使用。
        </p>
      )}
    </div>
  )
}

export function ExperiencePage() {
  const experience = useQuery({ queryKey: ['experience'], queryFn: api.experience })
  const [formOpen, setFormOpen] = useState(false)
  const [archivedOpen, setArchivedOpen] = useState(false)
  const data = experience.data
  const candidates = data?.entries.filter((e) => e.status === 'candidate') ?? []
  const active = data?.entries.filter((e) => e.status === 'active') ?? []
  const archived = data?.entries.filter((e) => e.status === 'retired' || e.status === 'promoted') ?? []

  return (
    <PageScaffold
      title="经验"
      width="md"
      description="从过往排查中总结出的做法。agent 会按相关度参考它们；被验证有用的经验可以升级为技能。"
      actions={
        data && (
          <Button onClick={() => setFormOpen(true)} disabled={formOpen}>
            <Plus className="size-4" aria-hidden />
            手动添加
          </Button>
        )
      }
    >
      {experience.isLoading && <ListSkeleton rows={5} label="加载经验" />}
      {experience.error && <ErrorBox error={experience.error} />}

      {data && (
        <>
          <ReflectionStatus index={data} />

          {formOpen && <AddExperience index={data} onClose={() => setFormOpen(false)} />}

          {candidates.length > 0 && (
            <section aria-labelledby="exp-candidates" className="space-y-3">
              <h2 id="exp-candidates" className="flex items-center gap-2 text-sm font-semibold text-foreground">
                待确认
                <span className="rounded-full bg-amber-100 px-2 text-xs font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
                  {candidates.length}
                </span>
              </h2>
              <ul className="space-y-3">
                {candidates.map((entry) => (
                  <ExperienceCard key={`${entry.id}-${entry.updated_at}`} entry={entry} />
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="exp-active" className="space-y-3">
            <h2 id="exp-active" className="text-sm font-semibold text-foreground">
              生效中 <span className="font-normal text-muted-foreground">({active.length})</span>
            </h2>
            {active.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-6 py-10 text-center">
                <Lightbulb className="size-6 text-muted-foreground" aria-hidden />
                <p className="text-sm font-medium text-foreground">还没有生效中的经验</p>
                <p className="text-[13px] text-muted-foreground">
                  确认上方的候选经验，或运行 <code className="rounded bg-muted px-1 font-mono text-xs">log-agent eval learn</code>{' '}
                  从评测中学习。
                </p>
              </div>
            ) : (
              <ul className="space-y-3">
                {active.map((entry) => (
                  <ExperienceCard key={`${entry.id}-${entry.updated_at}`} entry={entry} />
                ))}
              </ul>
            )}
          </section>

          <ExperienceConsolidate index={data} />

          {archived.length > 0 && (
            <section aria-labelledby="exp-archived">
              <button
                type="button"
                id="exp-archived"
                aria-expanded={archivedOpen}
                onClick={() => setArchivedOpen((o) => !o)}
                className="inline-flex items-center gap-1.5 rounded-sm text-sm font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
              >
                <ChevronDown className={`size-4 transition-transform ${archivedOpen ? '' : '-rotate-90'}`} aria-hidden />
                已归档与已升级 ({archived.length})
              </button>
              {archivedOpen && (
                <ul className="mt-3 space-y-3">
                  {archived.map((entry) => (
                    <ExperienceCard key={`${entry.id}-${entry.updated_at}`} entry={entry} />
                  ))}
                </ul>
              )}
            </section>
          )}

          <p className="truncate font-mono text-xs text-muted-foreground" title={data.path}>
            存储位置：{data.path}
          </p>
        </>
      )}
    </PageScaffold>
  )
}

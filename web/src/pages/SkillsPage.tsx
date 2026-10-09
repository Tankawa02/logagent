import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, BookOpen, FileText, FolderOpen, Layers, Link2, Plus, Save, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Markdown } from '../components/Markdown'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Spinner } from '../components/ui'
import { api } from '../lib/api'
import type { SkillSource, SkillSummary } from '../lib/types'

type Selection = { kind: 'skill'; source: string; name: string } | { kind: 'new' } | null

const FIELD =
  'w-full rounded-md border border-zinc-200 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-800 dark:bg-zinc-950'
const NAME_RULE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const FRONTMATTER = /^---\s*\n[\s\S]*?\n---\s*(?:\n|$)/

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function template(name: string, description: string): string {
  return `---
name: ${name}
description: ${JSON.stringify(description || '在这里写一句话：什么问题、什么系统该用这本手册')}
---

# ${name}

## 适用场景

- 

## 关键日志字段

- 

## 排查步骤

1. 
2. 

## 常见根因

- 
`
}

/** 列表按生效优先级从高到低展示：后面的来源会覆盖前面的同名 skill */
function byPriority(sources: SkillSource[]): SkillSource[] {
  return [...sources].reverse()
}

function SkillRow({ skill, active, onSelect }: { skill: SkillSummary; active: boolean; onSelect: () => void }) {
  const broken = skill.problems.length > 0
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? 'true' : undefined}
        className={`flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm ${
          active ? 'bg-zinc-200/70 dark:bg-zinc-800' : 'hover:bg-zinc-100 dark:hover:bg-zinc-800/60'
        }`}
      >
        {broken ? (
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" aria-label="格式有问题" />
        ) : (
          <BookOpen className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-400" aria-hidden />
        )}
        <span className="min-w-0 flex-1">
          <span
            className={`block truncate font-mono text-xs ${skill.shadowed_by ? 'text-zinc-400 line-through' : 'text-zinc-800 dark:text-zinc-100'}`}
          >
            {skill.name}
          </span>
          <span className="line-clamp-2 text-xs text-zinc-500">
            {broken ? skill.problems[0] : skill.shadowed_by ? `被「${skill.shadowed_by}」的同名 skill 覆盖` : skill.description}
          </span>
        </span>
        {skill.readonly && <Link2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-400" aria-label="符号链接，只读" />}
        <span className="shrink-0 text-xs tabular-nums text-zinc-400" title={`${skill.files} 个文件`}>
          {skill.files}
        </span>
      </button>
    </li>
  )
}

function SourceList({
  sources,
  selection,
  onSelect,
}: {
  sources: SkillSource[]
  selection: Selection
  onSelect: (next: Selection) => void
}) {
  return (
    <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
      {byPriority(sources).map((source, index) => (
        <section key={source.key} className="px-2 py-3" aria-labelledby={`source-${source.key}`}>
          <div className="flex items-baseline justify-between gap-2 px-2">
            <h3 id={`source-${source.key}`} className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              {source.label}
              {index === 0 && sources.length > 1 && <span className="ml-1.5 font-normal text-zinc-400">优先级最高</span>}
            </h3>
            <span className="text-xs text-zinc-400">{source.skills.length}</span>
          </div>
          <p className="truncate px-2 font-mono text-xs text-zinc-500 dark:text-zinc-400" title={source.directory}>
            {source.directory}
          </p>
          {source.skills.length === 0 ? (
            <p className="px-2 pt-2 text-xs text-zinc-400">{source.exists ? '目录里还没有 skill' : '目录尚不存在，新建时自动创建'}</p>
          ) : (
            <ul className="mt-1.5 space-y-0.5">
              {source.skills.map((skill) => (
                <SkillRow
                  key={skill.name}
                  skill={skill}
                  active={selection?.kind === 'skill' && selection.source === source.key && selection.name === skill.name}
                  onSelect={() => onSelect({ kind: 'skill', source: source.key, name: skill.name })}
                />
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}

function NewSkillForm({
  sources,
  onCreated,
  onCancel,
}: {
  sources: SkillSource[]
  onCreated: (source: string, name: string) => void
  onCancel: () => void
}) {
  const client = useQueryClient()
  const [source, setSource] = useState(sources[0]?.key ?? 'user')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const validName = NAME_RULE.test(name) && name.length <= 64
  const target = sources.find((s) => s.key === source)
  const create = useMutation({
    mutationFn: () => api.createSkill(source, name, template(name, description.trim())),
    onSuccess: (detail) => {
      void client.invalidateQueries({ queryKey: ['skills'] })
      onCreated(detail.source, detail.name)
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (validName) create.mutate()
  }

  return (
    <Card>
      <CardHeader title="新建 skill" />
      <form onSubmit={submit} className="space-y-4 px-4 py-4">
        <div className="space-y-1.5">
          <label htmlFor="skill-source" className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
            保存到
          </label>
          <select id="skill-source" value={source} onChange={(e) => setSource(e.target.value)} className={FIELD}>
            {byPriority(sources).map((s) => (
              <option key={s.key} value={s.key}>
                {s.label} · {s.hint}
              </option>
            ))}
          </select>
          {target && <p className="truncate font-mono text-xs text-zinc-500 dark:text-zinc-400">{target.directory}</p>}
        </div>
        <div className="space-y-1.5">
          <label htmlFor="skill-name" className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
            名称
          </label>
          <input
            id="skill-name"
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase())}
            placeholder="payment-timeout"
            autoFocus
            maxLength={64}
            aria-invalid={name !== '' && !validName}
            aria-describedby="skill-name-hint"
            className={`${FIELD} font-mono`}
          />
          <p id="skill-name-hint" className={`text-xs ${name && !validName ? 'text-red-600' : 'text-zinc-400'}`}>
            小写字母、数字和单个连字符，同时作为目录名
          </p>
        </div>
        <div className="space-y-1.5">
          <label htmlFor="skill-desc" className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
            描述
          </label>
          <textarea
            id="skill-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            maxLength={1024}
            placeholder="支付回调超时、对账不一致时使用：网关日志字段、重试逻辑位置、常见根因"
            className={FIELD}
          />
          <p className="text-xs text-zinc-400">模型只看到名称和这句描述，靠它判断什么时候读全文</p>
        </div>
        {create.error && <ErrorBox error={create.error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onCancel}>取消</Button>
          <Button type="submit" variant="primary" disabled={!validName || create.isPending}>
            {create.isPending ? <Spinner className="h-3 w-3" /> : <Plus className="h-4 w-4" aria-hidden />}
            创建并编辑
          </Button>
        </div>
      </form>
    </Card>
  )
}

function SkillEditor({
  source,
  name,
  onDirtyChange,
  onDeleted,
}: {
  source: string
  name: string
  onDirtyChange: (dirty: boolean) => void
  onDeleted: () => void
}) {
  const client = useQueryClient()
  const detail = useQuery({ queryKey: ['skill', source, name], queryFn: () => api.skill(source, name) })
  const [draft, setDraft] = useState<string | null>(null)
  const [tab, setTab] = useState<'edit' | 'preview'>('edit')
  const content = draft ?? detail.data?.content ?? ''
  const dirty = draft !== null && draft !== detail.data?.content

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange])

  const save = useMutation({
    mutationFn: () => api.saveSkill(source, name, content),
    onSuccess: (data) => {
      client.setQueryData(['skill', source, name], data)
      setDraft(null)
      void client.invalidateQueries({ queryKey: ['skills'] })
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteSkill(source, name),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['skills'] })
      client.removeQueries({ queryKey: ['skill', source, name] })
      onDeleted()
    },
  })

  if (detail.isLoading) {
    return (
      <Card className="flex justify-center py-16">
        <Spinner />
      </Card>
    )
  }
  if (detail.error) return <ErrorBox error={detail.error} />
  const data = detail.data!
  const body = content.replace(FRONTMATTER, '')

  return (
    <Card className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-100 px-4 py-2.5 dark:border-zinc-800">
        <h2 className="font-mono text-sm font-semibold text-zinc-800 dark:text-zinc-100">{data.name}</h2>
        <Badge>{data.source_label}</Badge>
        {dirty && <Badge tone="amber">未保存</Badge>}
        <div className="ml-auto flex items-center gap-2">
          <div role="tablist" aria-label="视图" className="flex rounded-md bg-zinc-100 p-0.5 text-xs dark:bg-zinc-800">
            {(['edit', 'preview'] as const).map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={`rounded px-2 py-1 ${tab === key ? 'bg-white font-medium text-zinc-900 shadow-xs dark:bg-zinc-950 dark:text-zinc-100' : 'text-zinc-500'}`}
              >
                {key === 'edit' ? '编辑' : '预览'}
              </button>
            ))}
          </div>
          {!data.readonly && (
            <>
              <Button
                variant="danger"
                disabled={remove.isPending}
                onClick={() => {
                  const extra = data.files.length > 1 ? `，目录里的 ${data.files.length} 个文件会一并删除` : ''
                  if (window.confirm(`删除 skill「${data.name}」${extra}？此操作不可撤销。`)) remove.mutate()
                }}
              >
                <Trash2 className="h-4 w-4" aria-hidden />
                删除
              </Button>
              <Button variant="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()} title="Ctrl / ⌘ + S">
                {save.isPending ? <Spinner className="h-3 w-3" /> : <Save className="h-4 w-4" aria-hidden />}
                保存
              </Button>
            </>
          )}
        </div>
      </div>

      {(data.readonly || save.error || remove.error || data.problems.length > 0) && (
        <div className="space-y-2 border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
          {data.readonly && (
            <p className="flex items-center gap-2 text-sm text-zinc-500">
              <Link2 className="h-4 w-4 shrink-0" aria-hidden />
              这是符号链接的 skill，只能查看；如需修改，请到链接目标处在本地编辑。
            </p>
          )}
          {save.error && <ErrorBox error={save.error} />}
          {remove.error && <ErrorBox error={`删除失败：${errorText(remove.error)}`} />}
          {data.problems.length > 0 && !save.error && (
            <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <div>
                <p className="font-medium">这本手册目前不会被加载</p>
                <ul className="mt-0.5 list-disc pl-4 text-xs">
                  {data.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'edit' ? (
        <textarea
          value={content}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
              e.preventDefault()
              if (dirty && !save.isPending && !data.readonly) save.mutate()
            }
          }}
          spellCheck={false}
          readOnly={data.readonly}
          aria-label={`${data.name} 的 SKILL.md`}
          className="min-h-[28rem] flex-1 resize-y bg-transparent px-4 py-3 font-mono text-[13px] leading-relaxed text-zinc-800 outline-none dark:text-zinc-200"
        />
      ) : (
        <div className="min-h-[28rem] flex-1 overflow-auto px-5 py-4">
          {body.trim() ? <Markdown text={body} /> : <Empty>正文为空</Empty>}
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-zinc-100 px-4 py-2 text-xs text-zinc-400 dark:border-zinc-800">
        <span className="flex min-w-0 items-center gap-1">
          <FolderOpen className="h-3 w-3 shrink-0" aria-hidden />
          <span className="truncate font-mono" title={data.path}>
            {data.path}
          </span>
        </span>
        {data.files.length > 1 && (
          <span className="flex items-center gap-1" title={data.files.join('\n')}>
            <FileText className="h-3 w-3" aria-hidden />
            {data.files.length} 个文件
          </span>
        )}
        {data.updated_at && <span className="ml-auto">更新于 {data.updated_at}</span>}
      </footer>
    </Card>
  )
}

export function SkillsPage() {
  const skills = useQuery({ queryKey: ['skills'], queryFn: api.skills })
  const [selection, setSelection] = useState<Selection>(null)
  const [dirty, setDirty] = useState(false)
  const sources = useMemo(() => skills.data?.sources ?? [], [skills.data])
  const total = sources.reduce((sum, s) => sum + s.skills.length, 0)
  const active = sources.reduce((sum, s) => sum + s.skills.filter((k) => !k.problems.length && !k.shadowed_by).length, 0)

  useEffect(() => {
    if (selection || !sources.length) return
    const first = byPriority(sources).find((s) => s.skills.length)
    if (first) setSelection({ kind: 'skill', source: first.key, name: first.skills[0].name })
  }, [sources, selection])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const select = (next: Selection) => {
    if (dirty && !window.confirm('当前 skill 有未保存的修改，确定离开？')) return
    setDirty(false)
    setSelection(next)
  }

  return (
    <main className="h-full overflow-auto">
      <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 sm:px-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">Skills</h1>
            <p className="mt-1 text-sm text-zinc-500">
              团队写好的排查手册。模型只看名称和描述，问题相符时才读取全文，装多少本都不会撑大上下文。
            </p>
          </div>
          <Button variant="primary" onClick={() => select({ kind: 'new' })}>
            <Plus className="h-4 w-4" aria-hidden />
            新建 skill
          </Button>
        </header>

        {skills.isLoading && (
          <div className="flex justify-center py-16">
            <Spinner />
          </div>
        )}
        {skills.error && <ErrorBox error={skills.error} />}

        {skills.data && (
          <div className="grid items-start gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
            <Card className="lg:sticky lg:top-0">
              <CardHeader
                title={
                  <span className="flex items-center gap-1.5">
                    <Layers className="h-3.5 w-3.5" aria-hidden />
                    来源
                  </span>
                }
              >
                <span className="text-xs text-zinc-400">
                  {active} / {total} 生效
                </span>
              </CardHeader>
              <SourceList sources={sources} selection={selection} onSelect={select} />
            </Card>

            {selection?.kind === 'new' ? (
              <NewSkillForm
                sources={sources}
                onCancel={() => setSelection(null)}
                onCreated={(source, name) => setSelection({ kind: 'skill', source, name })}
              />
            ) : selection?.kind === 'skill' ? (
              <SkillEditor
                key={`${selection.source}/${selection.name}`}
                source={selection.source}
                name={selection.name}
                onDirtyChange={setDirty}
                onDeleted={() => {
                  setDirty(false)
                  setSelection(null)
                }}
              />
            ) : (
              <Card>
                <Empty>
                  还没有 skill。点「新建 skill」写第一本排查手册，
                  <br />
                  或把现成的 <code className="font-mono">{'<名称>/SKILL.md'}</code> 放进左侧任一目录。
                </Empty>
              </Card>
            )}
          </div>
        )}
      </div>
    </main>
  )
}

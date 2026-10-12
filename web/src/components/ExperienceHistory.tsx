import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  ArrowRight,
  CircleCheck,
  CircleSlash,
  ExternalLink,
  FlaskConical,
  GitMerge,
  Pencil,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { ASSESSMENT, CONFIDENCE, shortModel, type Tone } from '../lib/format'
import type { Assessment, Confidence, ExperienceEvent, ExperienceItem, ExperienceRun, ExperienceStatus, GateGroup } from '../lib/types'
import { Badge, ErrorBox, ListSkeleton } from './ui'

const STATUS_LABEL: Record<ExperienceStatus, string> = { candidate: '候选', active: '生效', retired: '已淘汰', promoted: '已提升为 skill' }
const STATUS_BY: Record<string, string> = {
  manual: '人工操作',
  gate: '离线门控通过',
  harmful: '有害票过多，自动淘汰',
  cap: '超出生效上限，得分最低被自动淘汰',
  consolidated: '精简合并',
}
const VERDICT: Record<string, { label: string; tone: Tone }> = {
  helpful: { label: '有用', tone: 'green' },
  harmful: { label: '有害', tone: 'amber' },
  unused: { label: '没用上', tone: 'gray' },
}
const STEP_PREVIEW = 12

function sameText(a: string, b: string): boolean {
  return a.replace(/\s+/g, '') === b.replace(/\s+/g, '')
}

function sourceLabel(run: ExperienceRun): ReactNode {
  if (run.origin === 'eval') {
    return (
      <>
        评测案例 <span className="font-mono">{run.link?.case ?? run.source}</span>
        {run.link?.batch && <span className="text-zinc-400"> · 批次 {run.link.batch}</span>}
      </>
    )
  }
  if (run.link?.session) {
    return (
      <>
        会话 <span className="font-mono">{run.link.session}</span>
        {run.link.turn ? <span className="text-zinc-400"> · 第 {run.link.turn} 轮</span> : null}
      </>
    )
  }
  return <>命令行排查 <span className="font-mono">{run.source}</span></>
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <h4 className="text-xs font-semibold text-zinc-500 dark:text-zinc-400">{title}</h4>
        {aside}
      </div>
      {children}
    </div>
  )
}

function Steps({ run }: { run: ExperienceRun }) {
  const steps = run.context.steps ?? []
  const [all, setAll] = useState(false)
  if (steps.length === 0) return <p className="text-xs text-zinc-400">没有记录到工具调用。</p>
  const shown = all ? steps : steps.slice(0, STEP_PREVIEW)
  const total = run.context.step_count ?? steps.length
  return (
    <div className="space-y-1.5">
      <ol className="space-y-0.5 rounded-lg border border-zinc-100 bg-zinc-50/70 p-2 font-mono text-xs dark:border-zinc-800 dark:bg-zinc-950/40">
        {shown.map((step, index) => (
          <li key={index} className={`flex gap-2 ${step.subagent ? 'pl-4' : ''}`}>
            <span className="w-6 shrink-0 text-right text-zinc-400">{index + 1}</span>
            <span className={`shrink-0 font-medium ${step.failed ? 'text-red-600 dark:text-red-400' : 'text-zinc-800 dark:text-zinc-100'}`}>
              {step.subagent && <span className="text-zinc-400">↳ </span>}
              {step.name}
            </span>
            <span className="min-w-0 truncate text-zinc-500 dark:text-zinc-400" title={step.args}>
              {step.args}
            </span>
            {step.failed && <span className="shrink-0 text-red-600 dark:text-red-400">失败</span>}
          </li>
        ))}
      </ol>
      {(steps.length > STEP_PREVIEW || total > steps.length) && (
        <div className="flex items-center gap-3 text-xs text-zinc-400">
          {steps.length > STEP_PREVIEW && (
            <button type="button" onClick={() => setAll((v) => !v)} className="font-medium text-brand-600 hover:underline dark:text-brand-400">
              {all ? '收起' : `展开全部 ${steps.length} 步`}
            </button>
          )}
          {total > steps.length && <span>共 {total} 步，只保存了前 {steps.length} 步</span>}
        </div>
      )}
    </div>
  )
}

function score(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(0)
}

function RunDetail({ run, entry }: { run: ExperienceRun; entry: ExperienceItem }) {
  const ctx = run.context
  const assessment = ctx.assessment ? ASSESSMENT[ctx.assessment as Assessment] : undefined
  const evidence = ctx.evidence
  const truth = ctx.truth
  const votes = run.reflection.votes ?? []
  const drafts = run.reflection.new ?? []
  const summary = run.summary

  return (
    <div className="space-y-4 rounded-lg border border-zinc-200/80 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
        <span>{sourceLabel(run)}</span>
        <span className="text-zinc-400">{run.created_at.slice(0, 16)}</span>
        {run.link?.session && run.link.turn ? (
          <Link
            to="/trace/$name/$turn"
            params={{ name: run.link.session, turn: String(run.link.turn) }}
            className="ml-auto inline-flex items-center gap-1 font-medium text-brand-600 hover:underline dark:text-brand-400"
          >
            打开该轮 trace
            <ExternalLink className="h-3 w-3" aria-hidden />
          </Link>
        ) : null}
      </div>

      {ctx.question && (
        <Section title="用户问题">
          <p className="whitespace-pre-wrap break-words text-sm text-zinc-800 dark:text-zinc-100">{ctx.question}</p>
        </Section>
      )}

      <Section
        title="排查结果"
        aside={
          <>
            {assessment && <Badge tone={assessment.tone}>{assessment.label}</Badge>}
            {ctx.confidence && <span className="text-xs text-zinc-400">置信度 {CONFIDENCE[ctx.confidence as Confidence] ?? ctx.confidence}</span>}
          </>
        }
      >
        {ctx.conclusion ? (
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-zinc-700 dark:text-zinc-200">{ctx.conclusion}</p>
        ) : (
          <p className="text-xs text-zinc-400">没有结论。</p>
        )}
        {evidence && evidence.total > 0 && (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            证据回查 {evidence.total} 条：通过 {evidence.verified} · 行号偏移 {evidence.shifted} · 不符 {evidence.mismatch} · 无法核对{' '}
            {evidence.unresolved}
            {ctx.budget_hit && ' · 触发了 tokens 预算收尾'}
          </p>
        )}
      </Section>

      {truth && (
        <Section title="评测标准答案" aside={<Badge tone={(truth.score ?? 0) >= 80 ? 'green' : 'amber'}>{score(truth.score)} / 100</Badge>}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-zinc-400">判定</dt>
            <dd className="text-zinc-700 dark:text-zinc-200">
              {truth.assessment || '未标注'} · {truth.assessment_ok ? '答对' : '答错或缺失'}
            </dd>
            <dt className="text-zinc-400">根因位置</dt>
            <dd className="break-all font-mono text-zinc-700 dark:text-zinc-200">
              {truth.root_cause.join(', ') || '未标注'}
              <span className="ml-1 font-sans text-zinc-400">{truth.root_cause_hit ? '（命中）' : '（未命中）'}</span>
            </dd>
            {truth.keywords.length > 0 && (
              <>
                <dt className="text-zinc-400">关键词</dt>
                <dd className="text-zinc-700 dark:text-zinc-200">{truth.keywords.join('、')}</dd>
              </>
            )}
          </dl>
        </Section>
      )}

      <Section
        title="开始时注入��经验"
        aside={run.route && <span className="text-xs text-zinc-400">{run.route === 'model' ? '模型按语义挑选' : '按字面匹配挑选'}</span>}
      >
        {run.injected.length === 0 ? (
          <p className="text-xs text-zinc-400">无</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {run.injected.map((item) => (
              <li key={item.id}>
                <Badge tone={item.id === entry.id ? 'blue' : 'gray'} title={item.title}>
                  E{item.id} {item.title}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={`排查过程（${ctx.step_count ?? ctx.steps?.length ?? 0} 步）`}>
        <Steps run={run} />
      </Section>

      <Section title="复盘模型的判断" aside={run.model && <span className="font-mono text-xs text-zinc-400">{shortModel(run.model)}</span>}>
        {votes.length > 0 && (
          <ul className="flex flex-wrap gap-1.5">
            {votes.map((vote) => (
              <li key={vote.id}>
                <Badge tone={VERDICT[vote.verdict]?.tone ?? 'gray'}>
                  E{vote.id} {VERDICT[vote.verdict]?.label ?? vote.verdict}
                </Badge>
              </li>
            ))}
          </ul>
        )}
        {drafts.length > 0 ? (
          <ol className="space-y-1.5">
            {drafts.map((draft, index) => {
              const mine = sameText(draft.title, entry.title)
              return (
                <li
                  key={index}
                  className={`rounded-md border px-2.5 py-2 text-xs ${
                    mine
                      ? 'border-brand-200 bg-brand-50/60 dark:border-brand-900 dark:bg-brand-950/30'
                      : 'border-zinc-100 dark:border-zinc-800'
                  }`}
                >
                  <p className="flex flex-wrap items-center gap-1.5 font-medium text-zinc-800 dark:text-zinc-100">
                    {draft.title}
                    <Badge>{draft.scope === 'project' ? '本项目' : '全局'}</Badge>
                    {mine && <Badge tone="blue">即这条经验</Badge>}
                  </p>
                  <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">适用：{draft.trigger}</p>
                  <p className="mt-0.5 text-zinc-700 dark:text-zinc-200">{draft.strategy}</p>
                </li>
              )
            })}
          </ol>
        ) : (
          votes.length === 0 && <p className="text-xs text-zinc-400">没有提炼新经验。</p>
        )}
        {(summary.added?.length || summary.merged?.length || summary.rejected?.length) ? (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            落库：
            {[
              summary.added?.length ? `新增 ${summary.added.map((i) => `E${i}`).join('、')}` : '',
              summary.merged?.length ? `合并到 ${summary.merged.map((i) => `E${i}`).join('、')}` : '',
              summary.rejected?.length ? `安全检查拒绝 ${summary.rejected.length} 条（${summary.rejected.join('；')}）` : '',
            ]
              .filter(Boolean)
              .join('，')}
          </p>
        ) : null}
      </Section>

      {run.outcome && (
        <details className="group">
          <summary className="cursor-pointer select-none text-xs font-medium text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200">
            复盘模型收到的原文（已脱敏）
          </summary>
          <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-3 font-mono text-xs leading-relaxed text-zinc-700 dark:bg-zinc-950/60 dark:text-zinc-300">
            {run.outcome}
          </pre>
        </details>
      )}
    </div>
  )
}

function GateTable({ groups, cases }: { groups: Record<'train' | 'holdout' | 'all', GateGroup>; cases: { case: string; split: string; before: number | null; after: number | null }[] }) {
  const names = { train: '训练集', holdout: '留出集', all: '全体' } as const
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-zinc-400">
          <tr>
            <th className="py-1 pr-3 font-medium">分组 / 案例</th>
            <th className="py-1 pr-3 text-right font-medium">基线</th>
            <th className="py-1 pr-3 text-right font-medium">带上新经验</th>
            <th className="py-1 text-right font-medium">变化</th>
          </tr>
        </thead>
        <tbody className="tabular-nums text-zinc-700 dark:text-zinc-200">
          {(Object.keys(names) as (keyof typeof names)[]).map((key) => {
            const { before, after } = groups[key]
            const delta = before !== null && after !== null ? after - before : null
            return (
              <tr key={key} className="border-t border-zinc-100 font-medium dark:border-zinc-800">
                <td className="py-1 pr-3">{names[key]}</td>
                <td className="py-1 pr-3 text-right">{score(before)}</td>
                <td className="py-1 pr-3 text-right">{score(after)}</td>
                <td className={`py-1 text-right ${delta !== null && delta < 0 ? 'text-amber-700 dark:text-amber-400' : ''}`}>
                  {delta === null ? '—' : `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}`}
                </td>
              </tr>
            )
          })}
          {cases.map((row) => (
            <tr key={row.case} className="border-t border-zinc-100 text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              <td className="py-1 pr-3 font-mono">
                {row.case}
                <span className="ml-1 font-sans text-zinc-400">{row.split === 'holdout' ? '留出' : '训练'}</span>
              </td>
              <td className="py-1 pr-3 text-right">{score(row.before)}</td>
              <td className="py-1 pr-3 text-right">{score(row.after)}</td>
              <td className="py-1 text-right" />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function eventHeadline(event: ExperienceEvent, run: ExperienceRun | undefined): { icon: ReactNode; text: ReactNode; tone: string } {
  const origin = run ? sourceLabel(run) : null
  switch (event.kind) {
    case 'created':
      return {
        icon: event.detail.origin === 'eval' ? <FlaskConical className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />,
        tone: 'bg-brand-600 text-white',
        text:
          event.detail.origin === 'manual' ? (
            '手动添加'
          ) : (
            <>
              由{event.detail.origin === 'eval' ? '离线评测' : '在线复盘'}提炼，进入{STATUS_LABEL[event.detail.status as ExperienceStatus] ?? event.detail.status}
            </>
          ),
      }
    case 'merged':
      return {
        icon: <GitMerge className="h-3.5 w-3.5" />,
        tone: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-100',
        text: (
          <>
            另一次复盘提炼出相似经验，已合并{event.detail.counted_helpful ? '并记一张有用票' : ''}
            {origin && <span className="font-normal text-zinc-500 dark:text-zinc-400"> · {origin}</span>}
          </>
        ),
      }
    case 'helpful':
    case 'harmful':
      return {
        icon: event.kind === 'helpful' ? <ThumbsUp className="h-3.5 w-3.5" /> : <ThumbsDown className="h-3.5 w-3.5" />,
        tone: event.kind === 'helpful' ? 'bg-emerald-600 text-white' : 'bg-amber-500 text-white',
        text: (
          <>
            被注入后复盘标为{event.kind === 'helpful' ? '有用' : '有害'}
            {origin && <span className="font-normal text-zinc-500 dark:text-zinc-400"> · {origin}</span>}
          </>
        ),
      }
    case 'status':
      return {
        icon: event.detail.to === 'active' ? <CircleCheck className="h-3.5 w-3.5" /> : <CircleSlash className="h-3.5 w-3.5" />,
        tone: event.detail.to === 'active' ? 'bg-emerald-600 text-white' : 'bg-zinc-400 text-white dark:bg-zinc-600',
        text: (
          <span className="inline-flex flex-wrap items-center gap-1">
            {STATUS_LABEL[event.detail.from]}
            <ArrowRight className="h-3 w-3 text-zinc-400" aria-hidden />
            {STATUS_LABEL[event.detail.to]}
            <span className="font-normal text-zinc-500 dark:text-zinc-400">
              · {STATUS_BY[event.detail.by] ?? event.detail.by}
              {event.detail.into ? `，并入 E${event.detail.into}` : ''}
            </span>
          </span>
        ),
      }
    case 'consolidated':
      return {
        icon: <GitMerge className="h-3.5 w-3.5" />,
        tone: 'bg-brand-600 text-white',
        text: (
          <>
            精简：合并了 {event.detail.absorbed.map((a) => `E${a.id}`).join('、')}
            {event.detail.model && <span className="font-mono font-normal text-zinc-500 dark:text-zinc-400"> · {shortModel(event.detail.model)}</span>}
          </>
        ),
      }
    case 'gate':
      return {
        icon: <FlaskConical className="h-3.5 w-3.5" />,
        tone: event.detail.accepted ? 'bg-emerald-600 text-white' : 'bg-amber-500 text-white',
        text: (
          <>
            离线门控{event.detail.dry_run ? '（试运行）' : ''}：{event.detail.accepted ? '通过' : '未通过'}
            <span className="font-normal text-zinc-500 dark:text-zinc-400">
              {' '}
              · 批次 {event.detail.batch} · 同批候选 {event.detail.candidates.length} 条
            </span>
          </>
        ),
      }
    case 'edited':
      return { icon: <Pencil className="h-3.5 w-3.5" />, tone: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-100', text: '人工编辑' }
  }
}

function EventBody({ event, run, entry }: { event: ExperienceEvent; run: ExperienceRun | undefined; entry: ExperienceItem }) {
  if (event.kind === 'created') {
    if (event.detail.origin === 'manual') return null
    if (!run) return <p className="text-xs text-zinc-400">复盘记录已被清理。</p>
    return <RunDetail run={run} entry={entry} />
  }
  if (event.kind === 'merged') {
    return (
      <div className="space-y-2">
        <div className="rounded-md border border-zinc-100 px-2.5 py-2 text-xs dark:border-zinc-800">
          <p className="font-medium text-zinc-800 dark:text-zinc-100">{event.detail.draft.title}</p>
          <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">适用：{event.detail.draft.trigger}</p>
          <p className="mt-0.5 text-zinc-700 dark:text-zinc-200">{event.detail.draft.strategy}</p>
        </div>
        {run && <Collapsible label="查看这次排查的过程"><RunDetail run={run} entry={entry} /></Collapsible>}
      </div>
    )
  }
  if (event.kind === 'helpful' || event.kind === 'harmful') {
    return run ? <Collapsible label="查看这次排查的过程"><RunDetail run={run} entry={entry} /></Collapsible> : null
  }
  if (event.kind === 'gate') {
    return (
      <Collapsible label="查看门控得分">
        <GateTable groups={event.detail.groups} cases={event.detail.cases} />
        <p className="mt-1.5 text-xs text-zinc-400">
          留出集与全体的平均分都不低于基线{event.detail.tolerance ? `（允许 ${event.detail.tolerance} 分波动）` : ''}才通过
          {event.detail.model && <span className="font-mono"> · {shortModel(event.detail.model)}</span>}
        </p>
      </Collapsible>
    )
  }
  if (event.kind === 'consolidated') {
    const { before, absorbed, reason } = event.detail
    return (
      <div className="space-y-2 text-xs">
        {reason && <p className="text-zinc-600 dark:text-zinc-300">理由：{reason}</p>}
        <div className="rounded-md border border-zinc-100 px-2.5 py-2 dark:border-zinc-800">
          <p className="text-zinc-400">
            合并前的本条（{STATUS_LABEL[before.status]} · 有用 {before.helpful} · 有害 {before.harmful}）
          </p>
          <p className="mt-0.5 font-medium text-zinc-600 dark:text-zinc-300">{before.title}</p>
          <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">适用：{before.trigger}</p>
          <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">{before.strategy}</p>
        </div>
        {absorbed.map((item) => (
          <div key={item.id} className="rounded-md border border-zinc-100 px-2.5 py-2 dark:border-zinc-800">
            <p className="text-zinc-400">
              并入的 E{item.id}（{STATUS_LABEL[item.status]} · 有用 {item.helpful} · 有害 {item.harmful}）
            </p>
            <p className="mt-0.5 font-medium text-zinc-600 dark:text-zinc-300">{item.title}</p>
            <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">适用：{item.trigger}</p>
            <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">{item.strategy}</p>
          </div>
        ))}
      </div>
    )
  }
  if (event.kind === 'edited') {
    const before = event.detail.before
    return (
      <div className="rounded-md border border-zinc-100 px-2.5 py-2 text-xs dark:border-zinc-800">
        <p className="text-zinc-400">编辑前</p>
        <p className="mt-0.5 font-medium text-zinc-600 line-through decoration-zinc-300 dark:text-zinc-300">{before.title}</p>
        <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">适用：{before.trigger}</p>
        <p className="mt-0.5 text-zinc-500 dark:text-zinc-400">{before.strategy}</p>
      </div>
    )
  }
  return null
}

function Collapsible({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="group">
      <summary className="cursor-pointer select-none text-xs font-medium text-brand-600 hover:underline dark:text-brand-400">{label}</summary>
      <div className="mt-2">{children}</div>
    </details>
  )
}

export function ExperienceHistory({ entry }: { entry: ExperienceItem }) {
  const history = useQuery({ queryKey: ['experience', entry.id, 'history'], queryFn: () => api.experienceHistory(entry.id) })

  if (history.isLoading) return <ListSkeleton rows={3} label="加载经验来历" />
  if (history.error) return <ErrorBox error={history.error} />
  const data = history.data
  if (!data) return null
  const runs = new Map(data.runs.map((run) => [run.id, run]))

  if (data.events.length === 0) {
    return (
      <p className="rounded-lg bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-950/40 dark:text-zinc-400">
        这条经验产生于来历记录功能上线之前，只知道它{entry.origin === 'manual' ? '是手动添加的' : `来自${entry.origin === 'eval' ? '离线评测' : '在线复盘'}`}
        {entry.sources.length > 0 && `（来源：${entry.sources.join('、')}）`}，创建于 {entry.created_at.slice(0, 16)}。
      </p>
    )
  }

  return (
    <ol className="relative space-y-4 before:absolute before:bottom-2 before:left-[11px] before:top-2 before:w-px before:bg-zinc-200 dark:before:bg-zinc-800">
      {data.events.map((event) => {
        const run = event.run_id !== null ? runs.get(event.run_id) : undefined
        const head = eventHeadline(event, run)
        return (
          <li key={event.id} className="relative flex gap-3">
            <span className={`relative z-10 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${head.tone}`} aria-hidden>
              {head.icon}
            </span>
            <div className="min-w-0 flex-1 space-y-2">
              <p className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm font-medium text-zinc-800 dark:text-zinc-100">
                <span className="min-w-0">{head.text}</span>
                <time className="shrink-0 text-xs font-normal text-zinc-400" dateTime={event.created_at.replace(' ', 'T')}>
                  {event.created_at.slice(0, 16)}
                </time>
              </p>
              <EventBody event={event} run={run} entry={entry} />
            </div>
          </li>
        )
      })}
    </ol>
  )
}

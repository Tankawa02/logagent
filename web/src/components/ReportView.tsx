import { ClipboardCheck, FlaskConical, HelpCircle, Lightbulb, ListChecks, Repeat, Target, Wrench, type LucideIcon } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { ASSESSMENT, CHECK_STATUS, CONFIDENCE, EVIDENCE, formatDuration, formatGenerated, splitPath, visibleReport } from '../lib/format'
import type { Evidence, EvidenceCheck, EvidenceItem, Issue, SourceTarget, TurnPayload } from '../lib/types'
import { Markdown } from './Markdown'
import { Badge, List } from './ui'

function itemFor(check: EvidenceCheck | null, issue: number, index: number): EvidenceItem | undefined {
  return check?.items.find((it) => it.issue === issue && it.index === index)
}

export function ReportView({
  payload,
  activeEvidence,
  onOpen,
}: {
  payload: TurnPayload
  activeEvidence?: string
  onOpen: (target: SourceTarget) => void
}) {
  const analysis = payload.analysis
  const check = payload.evidence_check
  const report = visibleReport(payload.report || '')

  return (
    <div className="space-y-6 p-4 sm:p-5">
      <header className="space-y-2.5">
        <p className="text-pretty text-lg font-semibold leading-snug tracking-tight text-zinc-900 dark:text-zinc-50">{payload.question}</p>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
          {analysis && <Badge tone={ASSESSMENT[analysis.assessment].tone}>{ASSESSMENT[analysis.assessment].label}</Badge>}
          {analysis && <Badge>可信度 {CONFIDENCE[analysis.confidence]}</Badge>}
          {check && (
            <Badge tone={CHECK_STATUS[check.status].tone} title={check.error}>
              {CHECK_STATUS[check.status].label} · {check.verified + check.shifted}/{check.total}
            </Badge>
          )}
          {payload.status !== 'ok' && <Badge tone="amber">{payload.status === 'interrupted' ? '已中断' : payload.error || '出错'}</Badge>}
          {payload.budget_hit && <Badge tone="amber">触达 tokens 预算</Badge>}
          <span>{formatGenerated(payload.generated_at)}</span>
          <span>· {payload.model}</span>
          {payload.provenance !== 'legacy_unknown' && (
            <span>
              · {formatDuration(payload.elapsed_seconds)} · 工具 {payload.tool_calls.length} 次 · tokens{' '}
              {(payload.usage.total ?? 0).toLocaleString()}
            </span>
          )}
        </div>
      </header>

      {!analysis && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
          结构化数据不可用（{payload.structured_status === 'invalid' ? '附录格式不合法' : '模型没有输出附录'}），
          以下为原始回答，异常状态无法自动判定。
        </div>
      )}

      {analysis && (
        <>
          <section
            aria-labelledby="conclusion-heading"
            className="rounded-2xl border border-brand-200 bg-brand-50/70 p-4 dark:border-brand-900 dark:bg-brand-950/30"
          >
            <h3 id="conclusion-heading" className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-brand-700 dark:text-brand-300">
              <Target className="h-3.5 w-3.5" aria-hidden />
              结论
            </h3>
            <p className="text-pretty text-[15px] font-medium leading-7 text-zinc-900 dark:text-zinc-50">{analysis.conclusion}</p>
            {analysis.impact && (
              <p className="mt-3 border-t border-brand-200/70 pt-3 text-sm leading-relaxed text-zinc-700 dark:border-brand-900/70 dark:text-zinc-300">
                <span className="mr-1.5 font-medium text-zinc-900 dark:text-zinc-100">影响范围</span>
                {analysis.impact}
              </p>
            )}
          </section>
          {analysis.next_steps.length > 0 && (
            <Section title="下一步" icon={ListChecks}>
              <ol className="space-y-2">
                {analysis.next_steps.map((step, i) => (
                  <li key={i} className="flex gap-2.5 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-[11px] font-semibold tabular-nums text-white dark:bg-zinc-100 dark:text-zinc-900">
                      {i + 1}
                    </span>
                    <span className="min-w-0">
                      <RichText text={step} />
                    </span>
                  </li>
                ))}
              </ol>
            </Section>
          )}
          {analysis.issues.map((issue, i) => (
            <IssueCard key={i} issue={issue} number={i + 1} check={check} activeEvidence={activeEvidence} onOpen={onOpen} />
          ))}
          {analysis.open_questions.length > 0 && (
            <Section title="分析待确认项" icon={HelpCircle}>
              <List items={analysis.open_questions} />
            </Section>
          )}
        </>
      )}

      {analysis ? (
        <Collapsible title="完整分析与推导">
          <Markdown text={report || '_（没有生成报告内容）_'} onOpen={onOpen} />
        </Collapsible>
      ) : (
        <Markdown text={report || '_（没有生成报告内容）_'} onOpen={onOpen} />
      )}

      {payload.tool_calls.length > 0 && (
        <Collapsible title={`取证过程（${payload.tool_calls.length} 次工具调用）`}>
          <ol className="space-y-1 text-xs">
            {payload.tool_calls.map((tool, i) => (
              <li key={i} className="flex gap-2">
                <span className={`font-mono ${tool.failed ? 'text-red-600' : 'text-zinc-500'}`}>
                  {tool.subagent ? `  ↳ ${tool.name}` : tool.name}
                </span>
                <span className="min-w-0 flex-1 truncate text-zinc-700 dark:text-zinc-300" title={tool.summary}>
                  {tool.note ? `${tool.note} — ` : ''}
                  {tool.summary}
                </span>
                <span className="tabular-nums text-zinc-400">{tool.seconds.toFixed(1)}s</span>
              </li>
            ))}
          </ol>
        </Collapsible>
      )}
    </div>
  )
}

function IssueCard({
  issue,
  number,
  check,
  activeEvidence,
  onOpen,
}: {
  issue: Issue
  number: number
  check: EvidenceCheck | null
  activeEvidence?: string
  onOpen: (target: SourceTarget) => void
}) {
  return (
    <article className="overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
      <header className="space-y-3 border-b border-zinc-100 p-4 dark:border-zinc-800">
        <h3 className="flex items-start gap-2.5 text-[15px] font-semibold leading-snug text-zinc-900 dark:text-zinc-50">
          <span className="mt-px flex h-6 shrink-0 items-center rounded-md bg-red-50 px-1.5 text-xs font-semibold tabular-nums text-red-700 ring-1 ring-inset ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900">
            问题 {number}
          </span>
          <span className="min-w-0 text-pretty">{issue.title}</span>
        </h3>
        <dl className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-xl bg-zinc-50 px-3 py-2.5 dark:bg-zinc-800/50">
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">现象</dt>
            <dd className="mt-1 text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">
              <RichText text={issue.symptoms} />
            </dd>
          </div>
          <div className="rounded-xl bg-zinc-50 px-3 py-2.5 dark:bg-zinc-800/50">
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">影响</dt>
            <dd className="mt-1 text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">
              <RichText text={issue.impact} />
            </dd>
          </div>
        </dl>
      </header>

      <div className="space-y-6 p-4">
        <Section title="根因假设" icon={Lightbulb}>
          <ol className="space-y-3">
            {issue.root_cause_hypotheses.map((h, k) => (
              <Hypothesis key={k} index={k + 1} explanation={h.explanation} reasoning={h.reasoning} confidence={h.confidence} />
            ))}
            {!issue.root_cause_hypotheses.length && <li className="text-sm text-zinc-400">待确认 / 暂无信息</li>}
          </ol>
        </Section>

        <Section title={`证据（${issue.evidence.length}）`} icon={ClipboardCheck}>
          <div className="space-y-2">
            {issue.evidence.map((evidence, j) => (
              <EvidenceCard
                key={j}
                evidence={evidence}
                item={itemFor(check, number, j + 1)}
                active={activeEvidence === `${number}-${j + 1}`}
                onOpen={(target) => onOpen({ ...target, evidenceKey: `${number}-${j + 1}` })}
              />
            ))}
            {!issue.evidence.length && <p className="text-sm text-zinc-400">没有给出证据</p>}
          </div>
        </Section>

        <div className="grid gap-3 sm:grid-cols-2">
          <ActionBlock title="处理建议" icon={Wrench} accent="emerald" items={issue.recommendations} />
          <ActionBlock title="验证方法" icon={FlaskConical} accent="brand" items={issue.verification_steps} />
          <ActionBlock title="复现条件" icon={Repeat} accent="zinc" items={issue.reproduction_conditions} />
          <ActionBlock title="待确认项" icon={HelpCircle} accent="amber" items={issue.open_questions} />
        </div>
      </div>
    </article>
  )
}

const CONFIDENCE_STYLE = {
  high: { bar: 'bg-red-500', badge: 'red' },
  medium: { bar: 'bg-amber-500', badge: 'amber' },
  low: { bar: 'bg-zinc-300 dark:bg-zinc-600', badge: 'gray' },
} as const

/** 模型的推理通常是一长段用「；」串起来的多条依据，拆成要点更好读 */
function splitPoints(text: string): string[] {
  return text
    .split(/[；;]\s*|\n+/)
    .map((t) => t.trim().replace(/[。.]$/, ''))
    .filter(Boolean)
}

function Hypothesis({
  index,
  explanation,
  reasoning,
  confidence,
}: {
  index: number
  explanation: string
  reasoning: string
  confidence: keyof typeof CONFIDENCE_STYLE
}) {
  const style = CONFIDENCE_STYLE[confidence] ?? CONFIDENCE_STYLE.low
  const points = splitPoints(reasoning)
  return (
    <li className="relative overflow-hidden rounded-xl border border-zinc-200 bg-white pl-4 dark:border-zinc-800 dark:bg-zinc-900">
      <span className={`absolute inset-y-0 left-0 w-1 ${style.bar}`} aria-hidden />
      <div className="space-y-3 py-3 pr-4">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">假设 {index}</span>
          <Badge tone={style.badge}>可信度 {CONFIDENCE[confidence]}</Badge>
        </div>
        <p className="text-pretty text-[15px] font-medium leading-7 text-zinc-900 dark:text-zinc-50">
          <RichText text={explanation} />
        </p>
        {points.length > 0 && (
          <div className="rounded-lg bg-zinc-50 px-3 py-2.5 dark:bg-zinc-800/50">
            <p className="mb-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400">依据</p>
            <ul className="space-y-1.5">
              {points.map((point, i) => (
                <li key={i} className="flex gap-2 text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">
                  <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-zinc-400" aria-hidden />
                  <span className="min-w-0">
                    <RichText text={point} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </li>
  )
}

const ACCENT = {
  emerald: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300',
  brand: 'bg-brand-50 text-brand-700 dark:bg-brand-950/50 dark:text-brand-300',
  zinc: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300',
  amber: 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300',
} as const

function ActionBlock({
  title,
  icon: Icon,
  accent,
  items,
}: {
  title: string
  icon: LucideIcon
  accent: keyof typeof ACCENT
  items: string[]
}) {
  return (
    <section className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <h4 className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        <span className={`flex h-6 w-6 items-center justify-center rounded-md ${ACCENT[accent]}`}>
          <Icon className="h-3.5 w-3.5" aria-hidden />
        </span>
        {title}
      </h4>
      {items.length ? (
        <ul className="space-y-2">
          {items.map((item, i) => (
            <li key={i} className="flex gap-2 text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">
              <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-zinc-400" aria-hidden />
              <span className="min-w-0">
                <RichText text={item} />
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-zinc-400">待确认 / 暂无信息</p>
      )}
    </section>
  )
}

/** 文件:行号、日志:行号 和 a.b() / FOO_BAR / key=value 这类代码片段用等宽高亮，扫一眼就能看到关键位置 */
const TOKEN =
  /([\w$-]+(?:\.[\w$-]+)*\.[A-Za-z]{1,6}:\d+(?:-\d+)?|日志[:：]\d+(?:-\d+)?|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\(\)|[A-Za-z_][\w.]*\s?(?:==|!=|=)\s?[\w.-]+|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b)/g

function RichText({ text }: { text: string }) {
  const parts = text.split(TOKEN)
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code
            key={i}
            className="mx-0.5 rounded bg-zinc-100 px-1 py-px font-mono text-[0.85em] text-zinc-800 [overflow-wrap:anywhere] [box-decoration-break:clone] dark:bg-zinc-800 dark:text-zinc-200"
          >
            {part}
          </code>
        ) : (
          part
        ),
      )}
    </>
  )
}

function EvidenceCard({
  evidence,
  item,
  active,
  onOpen,
}: {
  evidence: Evidence
  item?: EvidenceItem
  active: boolean
  onOpen: (target: SourceTarget) => void
}) {
  const status = item ? EVIDENCE[item.status] : null
  // 行号偏移时跳到核对出的真实位置；对不上的证据仍然打开所引行，方便人工判断
  const start = item?.actual_start ?? evidence.line_start
  const end = item?.actual_end ?? evidence.line_end
  const range = `${evidence.line_start}${evidence.line_end !== evidence.line_start ? `-${evidence.line_end}` : ''}`
  const { dir, name } = splitPath(evidence.source)
  return (
    <button
      type="button"
      onClick={() => onOpen({ source: evidence.source, start, end })}
      className={`block w-full rounded-xl border bg-white text-left dark:bg-zinc-900 transition-colors ${
        active
          ? 'border-brand-400 bg-brand-50/60 ring-1 ring-brand-300 dark:border-brand-700 dark:bg-brand-950/30 dark:ring-brand-800'
          : 'border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:border-zinc-700 dark:hover:bg-zinc-800/40'
      }`}
    >
      {/* 源码路径往往很长：第一���只放文件名和行号（完整显示），目录弱化放到下一行；徽标和「查看原文」固定在右侧 */}
      <div className="flex items-start gap-2 px-3 pt-2" title={`${evidence.source}:${range}`}>
        <span className="min-w-0 flex-1">
          <span className="block break-all font-mono text-xs font-semibold leading-5 text-brand-700 dark:text-brand-300">
            {name}:{range}
          </span>
          {dir && <span className="block truncate font-mono text-[11px] leading-4 text-zinc-400 dark:text-zinc-500">{dir}</span>}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {status && (
            <Badge tone={status.tone} title={item?.note}>
              {status.mark} {status.label}
            </Badge>
          )}
          <span className="whitespace-nowrap text-[11px] leading-5 text-zinc-400">查看原文 →</span>
        </span>
      </div>
      {item?.note && <p className="px-3 pt-1 text-xs text-zinc-500 dark:text-zinc-400">{item.note}</p>}
      <pre className="mx-3 my-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-50 px-2 py-1.5 font-mono text-xs text-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
        {evidence.excerpt}
      </pre>
    </button>
  )
}

function Section({ title, icon: Icon, children }: { title: string; icon?: LucideIcon; children: ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h4 className="flex items-center gap-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        {Icon && <Icon className="h-4 w-4 text-zinc-400" aria-hidden />}
        {title}
      </h4>
      {children}
    </section>
  )
}

function Collapsible({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between rounded-xl px-4 py-2.5 text-left text-sm font-medium text-zinc-700 hover:bg-zinc-50 dark:text-zinc-200 dark:hover:bg-zinc-800/50"
      >
        {title}
        <span className="text-zinc-400">{open ? '收起' : '展开'}</span>
      </button>
      {open && <div className="border-t border-zinc-200 px-3 py-3 dark:border-zinc-800">{children}</div>}
    </div>
  )
}

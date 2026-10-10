import {
  Check,
  ClipboardCheck,
  Copy,
  FlaskConical,
  HelpCircle,
  Lightbulb,
  ListChecks,
  Repeat,
  Target,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ASSESSMENT,
  CHECK_STATUS,
  CONFIDENCE,
  EVIDENCE,
  cacheHitRate,
  formatDuration,
  formatGenerated,
  formatUsd,
  splitPath,
  visibleReport,
} from '../lib/format'
import { useCopy } from '../lib/hooks'
import type { Evidence, EvidenceCheck, EvidenceItem, EvidenceRepair, Issue, SimilarCase, SourceTarget, TurnPayload } from '../lib/types'
import { Markdown } from './Markdown'
import { Badge, List } from './ui'

function itemFor(check: EvidenceCheck | null, issue: number, index: number): EvidenceItem | undefined {
  return check?.items.find((it) => it.issue === issue && it.index === index)
}

/** 行号偏移时跳到核对出的真实位置；对不上的证据仍然打开所引行，方便人工判断 */
function targetFor(evidence: Evidence, item?: EvidenceItem): SourceTarget {
  return { source: evidence.source, start: item?.actual_start ?? evidence.line_start, end: item?.actual_end ?? evidence.line_end }
}

/** 本轮报告里所有证据的原文位置，按问题、证据顺序排列；原文面板用它做上一条 / 下一条 */
export function evidenceTargets(payload: TurnPayload | undefined | null): (SourceTarget & { evidenceKey: string })[] {
  const analysis = payload?.analysis
  if (!analysis) return []
  return analysis.issues.flatMap((issue, i) =>
    issue.evidence.map((evidence, j) => ({
      ...targetFor(evidence, itemFor(payload.evidence_check, i + 1, j + 1)),
      evidenceKey: `${i + 1}-${j + 1}`,
    })),
  )
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
  const cacheRate = cacheHitRate(payload.usage)

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
              {cacheRate != null && cacheRate > 0 && <> · 缓存命中 {Math.round(cacheRate * 100)}%</>}
              {payload.cost && (
                <span title={payload.cost.complete ? undefined : '部分调用的模型价格未知，未计入'}>
                  {' '}
                  · 费用 {formatUsd(payload.cost.usd)}
                  {payload.cost.usd != null && !payload.cost.complete && '+'}
                </span>
              )}
            </span>
          )}
          {payload.structured_source === 'extracted' && (
            <Badge title="模型没有写出合格的结构化附录，正文写完后单独抽取了一次">附录由补抽生成</Badge>
          )}
        </div>
      </header>

      {payload.evidence_repair?.adopted && <RepairNote repair={payload.evidence_repair} />}
      {payload.similar_cases && payload.similar_cases.length > 0 && <SimilarCases cases={payload.similar_cases} />}

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
            <p className={`text-pretty text-[15px] font-medium leading-7 text-zinc-900 dark:text-zinc-50 ${PROSE}`}>{analysis.conclusion}</p>
            {analysis.impact && (
              <p className={`mt-3 border-t border-brand-200/70 pt-3 text-sm leading-relaxed text-zinc-700 dark:border-brand-900/70 dark:text-zinc-300 ${PROSE}`}>
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
                    <span className={PROSE}>
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
    <article className="overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-700/70 dark:bg-zinc-800/50">
      <header className="space-y-3 border-b border-zinc-100 p-4 dark:border-zinc-700/70">
        <h3 className="flex items-start gap-2.5 text-[15px] font-semibold leading-snug text-zinc-900 dark:text-zinc-50">
          <span className="mt-px flex h-6 shrink-0 items-center rounded-md bg-zinc-100 px-1.5 text-xs font-semibold tabular-nums text-zinc-700 ring-1 ring-inset ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-200 dark:ring-zinc-700">
            问题 {number}
          </span>
          <span className={`text-pretty ${PROSE}`}>{issue.title}</span>
        </h3>
        <dl className="grid gap-2 sm:grid-cols-2">
          <div className="min-w-0 rounded-xl bg-zinc-50 px-3 py-2.5 dark:bg-zinc-900/70">
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">现象</dt>
            <dd className={`mt-1 text-sm leading-relaxed text-zinc-800 dark:text-zinc-200 ${PROSE}`}>
              <RichText text={issue.symptoms} />
            </dd>
          </div>
          <div className="min-w-0 rounded-xl bg-zinc-50 px-3 py-2.5 dark:bg-zinc-900/70">
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">影响</dt>
            <dd className={`mt-1 text-sm leading-relaxed text-zinc-800 dark:text-zinc-200 ${PROSE}`}>
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
  high: { bar: 'bg-brand-600 dark:bg-brand-400', badge: 'blue' },
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
        <p className={`text-pretty text-[15px] font-medium leading-7 text-zinc-900 dark:text-zinc-50 ${PROSE}`}>
          <RichText text={explanation} />
        </p>
        {points.length > 0 && (
          <div className="rounded-lg bg-zinc-50 px-3 py-2.5 dark:bg-zinc-800/50">
            <p className="mb-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400">依据</p>
            <ul className="space-y-1.5">
              {points.map((point, i) => (
                <li key={i} className="flex gap-2 text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300">
                  <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-zinc-400" aria-hidden />
                  <span className={PROSE}>
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
    <section className="min-w-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
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
              <span className={PROSE}>
                <RichText text={item} />
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-zinc-500 dark:text-zinc-400">待确认 / 暂无信息</p>
      )}
    </section>
  )
}

/** 报告正文容器：模型常写出 A/B 类名、长 URL 这类没有空格的长串，必须允许任意位置断行，否则会撑出卡片 */
const PROSE = 'min-w-0 [overflow-wrap:anywhere]'

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
  const range = `${evidence.line_start}${evidence.line_end !== evidence.line_start ? `-${evidence.line_end}` : ''}`
  const { dir, name } = splitPath(evidence.source)
  const { state, copy } = useCopy()
  return (
    <div
      className={`overflow-hidden rounded-xl border bg-white transition-colors dark:bg-zinc-900 ${
        active
          ? 'border-brand-400 bg-brand-50/60 ring-1 ring-brand-300 dark:border-brand-700 dark:bg-brand-950/30 dark:ring-brand-800'
          : 'border-zinc-200 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700'
      }`}
    >
      {/* 只有标题行可点：摘录区留给用户选中、复制文字 */}
      <button
        type="button"
        onClick={() => onOpen(targetFor(evidence, item))}
        aria-current={active ? 'true' : undefined}
        title={`${evidence.source}:${range}`}
        className="group flex w-full items-start gap-2 px-3 pb-1 pt-2 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800/40"
      >
        <span className="min-w-0 flex-1">
          <span className="block break-all font-mono text-xs font-semibold leading-5 text-brand-700 group-hover:underline dark:text-brand-300">
            {name}:{range}
          </span>
          {dir && <span className="block truncate font-mono text-xs leading-4 text-zinc-500 dark:text-zinc-400">{dir}</span>}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {status && (
            <Badge tone={status.tone} title={item?.note}>
              {status.mark} {status.label}
            </Badge>
          )}
          <span className="whitespace-nowrap text-xs leading-5 text-zinc-500 group-hover:text-brand-700 dark:text-zinc-400 dark:group-hover:text-brand-300">
            查看原文 →
          </span>
        </span>
      </button>
      {item?.note && <p className="px-3 pt-1 text-xs text-zinc-500 dark:text-zinc-400">{item.note}</p>}
      <div className="group/excerpt relative mx-3 my-2">
        <pre className="max-h-40 select-text overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-50 py-1.5 pl-2 pr-16 font-mono text-xs text-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
          {evidence.excerpt}
        </pre>
        <button
          type="button"
          onClick={() => void copy(evidence.excerpt)}
          aria-label="复制摘录"
          className="absolute right-1.5 top-1.5 flex items-center gap-1 rounded-md bg-white/90 px-1.5 py-0.5 text-[11px] text-zinc-500 opacity-0 shadow-xs ring-1 ring-inset ring-zinc-200 transition-opacity hover:text-zinc-900 focus-visible:opacity-100 group-hover/excerpt:opacity-100 dark:bg-zinc-900/90 dark:ring-zinc-700 dark:hover:text-zinc-100 [@media(hover:none)]:opacity-100"
        >
          {state === 'copied' ? <Check className="h-3 w-3" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
          {state === 'copied' ? '已复制' : state === 'failed' ? '复制失败' : '复制'}
        </button>
      </div>
    </div>
  )
}

function RepairNote({ repair }: { repair: EvidenceRepair }) {
  const parts: string[] = []
  const lines = (repair.shifted_fixed ?? 0) + (repair.relocated ?? 0)
  if (lines) parts.push(`校正行号 ${lines} 条`)
  if (repair.model_fixed) parts.push(`对照原文修正摘录 ${repair.model_fixed} 条`)
  if (repair.dropped) parts.push(`移除无法对应原文的证据 ${repair.dropped} 条`)
  if (parts.length === 0) return null
  return (
    <p className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
      证据已自动修正：{parts.join('，')}。下方核对结果是修正后的状态。
    </p>
  )
}

function SimilarCases({ cases }: { cases: SimilarCase[] }) {
  return (
    <details className="rounded-md border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800">
      <summary className="cursor-pointer text-xs font-medium text-zinc-600 dark:text-zinc-400">
        参考了 {cases.length} 个历史相似案例
      </summary>
      <ul className="mt-2 space-y-2">
        {cases.map((c) => (
          <li key={`${c.session}-${c.turn}`} className="space-y-0.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <Link
                to="/sessions/$name"
                params={{ name: c.session }}
                search={{ turn: c.turn, panel: 'report' }}
                className="font-medium text-zinc-900 underline-offset-2 hover:underline dark:text-zinc-100"
              >
                {c.title || c.session}
              </Link>
              {c.confirmed && <Badge tone="green">已确认</Badge>}
              {c.correction && <Badge tone="amber">有纠正</Badge>}
              <span className="text-xs text-zinc-500">{c.matched.slice(0, 2).join(' · ')}</span>
            </div>
            <p className={`text-xs text-zinc-600 dark:text-zinc-400 ${PROSE}`}>{c.correction ? `纠正：${c.correction}` : c.conclusion}</p>
          </li>
        ))}
      </ul>
    </details>
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

import { useState, type ReactNode } from 'react'
import {
  ASSESSMENT,
  CHECK_STATUS,
  CONFIDENCE,
  EVIDENCE,
  formatDuration,
  formatGenerated,
  visibleReport,
} from '../lib/format'
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
    <div className="space-y-5 p-4">
      <header className="space-y-2">
        <p className="text-base font-semibold leading-snug text-zinc-900 dark:text-zinc-50">{payload.question}</p>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
          {analysis && <Badge tone={ASSESSMENT[analysis.assessment].tone}>{ASSESSMENT[analysis.assessment].label}</Badge>}
          {analysis && <Badge>可信度 {CONFIDENCE[analysis.confidence]}</Badge>}
          {check && (
            <Badge tone={CHECK_STATUS[check.status].tone} title={check.error}>
              {CHECK_STATUS[check.status].label} · {check.verified + check.shifted}/{check.total}
            </Badge>
          )}
          {payload.status !== 'ok' && (
            <Badge tone="amber">{payload.status === 'interrupted' ? '已中断' : payload.error || '出错'}</Badge>
          )}
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
          <Section title="结论">
            <p className="text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">{analysis.conclusion}</p>
          </Section>
          <Section title="影响范围">
            <p className="text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">{analysis.impact}</p>
          </Section>
          <Section title="下一步">
            <List items={analysis.next_steps} />
          </Section>
          {analysis.issues.map((issue, i) => (
            <IssueCard
              key={i}
              issue={issue}
              number={i + 1}
              check={check}
              activeEvidence={activeEvidence}
              onOpen={onOpen}
            />
          ))}
          {analysis.open_questions.length > 0 && (
            <Section title="分析待确认项">
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
    <article className="space-y-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
        问题 {number}：{issue.title}
      </h3>
      <dl className="grid gap-1 text-sm sm:grid-cols-[4rem_1fr]">
        <dt className="text-zinc-500">现象</dt>
        <dd className="text-zinc-800 dark:text-zinc-200">{issue.symptoms}</dd>
        <dt className="text-zinc-500">影响</dt>
        <dd className="text-zinc-800 dark:text-zinc-200">{issue.impact}</dd>
      </dl>
      <Section title={`证据（${issue.evidence.length}）`}>
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
      <Section title="根因假设">
        <ul className="space-y-2 text-sm">
          {issue.root_cause_hypotheses.map((h, k) => (
            <li key={k} className="rounded-md bg-zinc-50 px-3 py-2 dark:bg-zinc-800/60">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-zinc-800 dark:text-zinc-100">{h.explanation}</span>
                <Badge tone={h.confidence === 'high' ? 'red' : h.confidence === 'medium' ? 'amber' : 'gray'}>
                  可信度 {CONFIDENCE[h.confidence]}
                </Badge>
              </div>
              <p className="mt-1 text-zinc-600 dark:text-zinc-400">{h.reasoning}</p>
            </li>
          ))}
          {!issue.root_cause_hypotheses.length && <li className="text-zinc-400">待确认 / 暂无信息</li>}
        </ul>
      </Section>
      <div className="grid gap-3 sm:grid-cols-2">
        <Section title="处理建议">
          <List items={issue.recommendations} />
        </Section>
        <Section title="验证方法">
          <List items={issue.verification_steps} />
        </Section>
        <Section title="复现条件">
          <List items={issue.reproduction_conditions} />
        </Section>
        <Section title="待确认项">
          <List items={issue.open_questions} />
        </Section>
      </div>
    </article>
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
  return (
    <button
      type="button"
      onClick={() => onOpen({ source: evidence.source, start, end })}
      className={`block w-full rounded-md border text-left transition-colors ${
        active
          ? 'border-sky-400 bg-sky-50/60 ring-1 ring-sky-300 dark:border-sky-700 dark:bg-sky-950/30 dark:ring-sky-800'
          : 'border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:border-zinc-700 dark:hover:bg-zinc-800/40'
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 px-3 pt-2">
        <span className="font-mono text-xs text-sky-700 dark:text-sky-300">
          {evidence.source}:{range}
        </span>
        {status && (
          <Badge tone={status.tone} title={item?.note}>
            {status.mark} {status.label}
          </Badge>
        )}
        <span className="ml-auto text-[11px] text-zinc-400">查看原文 →</span>
      </div>
      {item?.note && <p className="px-3 pt-1 text-xs text-zinc-500 dark:text-zinc-400">{item.note}</p>}
      <pre className="mx-3 my-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-50 px-2 py-1.5 font-mono text-xs text-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
        {evidence.excerpt}
      </pre>
    </button>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">{title}</h4>
      {children}
    </div>
  )
}

function Collapsible({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-zinc-700 hover:bg-zinc-50 dark:text-zinc-200 dark:hover:bg-zinc-800/50"
      >
        {title}
        <span className="text-zinc-400">{open ? '收起' : '展开'}</span>
      </button>
      {open && <div className="border-t border-zinc-200 px-3 py-3 dark:border-zinc-800">{children}</div>}
    </div>
  )
}

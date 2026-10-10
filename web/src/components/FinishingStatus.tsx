import { Check } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Spinner } from './ui'

export type FinishPhase = 'structuring' | 'verifying' | 'repairing' | 'saving'

const STEPS: { phase: FinishPhase; label: string; hint: string }[] = [
  { phase: 'structuring', label: '整理结构化报告', hint: '正文已写完，模型正在生成结论、证据清单等可核对的字段' },
  { phase: 'verifying', label: '核对证据原文', hint: '逐条回到日志与源码里确认引用的行号和内容' },
  { phase: 'repairing', label: '修正证据引用', hint: '有证据与原文对不上，正在对照原文校正行号或摘录' },
  { phase: 'saving', label: '保存本轮', hint: '写入会话记录，完成后可查看结构化报告' },
]

export const FINISH_PHASES: ReadonlySet<string> = new Set(STEPS.map((s) => s.phase))

function useElapsed(key: string) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    setSeconds(0)
    const start = Date.now()
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [key])
  return seconds
}

/** 正文之后到存档之间的收尾进度：这段时间页面上没有新文字，必须明确告诉用户还在跑 */
export function FinishingStatus({ phase }: { phase: FinishPhase }) {
  // 修正只在回查有不符时才发生：其余情况不列出这一步，免得看起来像做过修正
  const steps = STEPS.filter((s) => s.phase !== 'repairing' || phase === 'repairing')
  const current = steps.findIndex((s) => s.phase === phase)
  const step = steps[current]
  const seconds = useElapsed(phase)

  return (
    <div
      role="status"
      aria-live="polite"
      className="animate-menu-in rounded-2xl border border-brand-200/70 bg-brand-50/60 px-4 py-3 dark:border-brand-900/60 dark:bg-brand-950/30"
    >
      <div className="flex items-center gap-2.5">
        <Spinner />
        <p className="min-w-0 flex-1 text-sm font-medium text-zinc-900 dark:text-zinc-100">
          还没结束 · 正在{step.label}
          <span className="ml-1.5 font-normal tabular-nums text-zinc-500 dark:text-zinc-400">{seconds} 秒</span>
        </p>
      </div>
      <p className="mt-1 pl-[26px] text-xs text-zinc-600 dark:text-zinc-400">{step.hint}</p>
      <ol aria-label="收尾步骤" className="mt-3 flex flex-wrap items-center gap-x-1.5 gap-y-1 pl-[26px] text-xs">
        {steps.map((s, i) => {
          const done = i < current
          const active = i === current
          return (
            <li key={s.phase} className="flex items-center gap-1.5">
              {i > 0 && <span className="h-px w-4 bg-zinc-300 dark:bg-zinc-700" aria-hidden />}
              <span
                className={`flex items-center gap-1 rounded-full px-2 py-0.5 ${
                  active
                    ? 'bg-white font-medium text-brand-700 ring-1 ring-inset ring-brand-200 dark:bg-zinc-900 dark:text-brand-300 dark:ring-brand-800'
                    : done
                      ? 'text-zinc-600 dark:text-zinc-300'
                      : 'text-zinc-400 dark:text-zinc-500'
                }`}
              >
                {done && <Check className="h-3 w-3 text-emerald-600" aria-hidden />}
                {s.label}
                <span className="sr-only">{done ? '（已完成）' : active ? '（进行中）' : '（未开始）'}</span>
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

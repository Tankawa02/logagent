import {
  Bot,
  Check,
  ChevronDown,
  FileCode2,
  FileText,
  FolderTree,
  GitCommitHorizontal,
  GitCompare,
  History,
  Lightbulb,
  Route,
  ScanSearch,
  Search,
  Wrench,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { visibleReport } from '../lib/format'
import { Spinner } from './ui'

export interface ToolActivity {
  id: string
  label: string
  name: string
  detail: string
  note?: string
  subagent?: string
  summary?: string
  failed?: boolean
  seconds?: number
  done: boolean
}

const TOOL_ICONS: Record<string, LucideIcon> = {
  log_overview: FileText,
  read_log_chunk: FileText,
  search_logs: Search,
  compare_windows: GitCompare,
  trace_request: Route,
  list_code_files: FolderTree,
  read_code_file: FileCode2,
  grep_code: ScanSearch,
  recent_changes: History,
  show_commit: GitCommitHorizontal,
  blame_lines: History,
  task: Bot,
  suggest_memory: Lightbulb,
}

/** 长路径只留最后两段，完整路径放在 title 里 */
function shortTarget(text: string): string {
  if (!/[\\/]/.test(text) || text.length <= 36) return text
  const parts = text.split(/[\\/]/).filter(Boolean)
  return parts.length > 2 ? `…/${parts.slice(-2).join('/')}` : text
}

function formatSeconds(seconds: number) {
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s` : `${seconds.toFixed(1)}s`
}

export function ActivityTimeline({ tools, draft, running }: { tools: ToolActivity[]; draft: string; running: boolean }) {
  // 用户没手动点过时：进行中展开，跑完自动收起，结论成为主角
  const [manualOpen, setManualOpen] = useState<boolean | null>(null)
  const open = manualOpen ?? running
  const failed = tools.filter((t) => t.failed).length
  const current = [...tools].reverse().find((t) => !t.done)
  const headline = running
    ? draft
      ? '正在整理结论'
      : current
        ? current.note || `${current.label || current.name}${current.detail ? ` · ${shortTarget(current.detail)}` : ''}`
        : '正在加载模型与工具'
    : `已完成取证`

  return (
    <section
      aria-label="取证过程"
      className="ml-10 overflow-hidden rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
    >
      <button
        type="button"
        onClick={() => setManualOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-zinc-50 dark:hover:bg-zinc-900"
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center">
          {running ? (
            <Spinner className="h-4 w-4" />
          ) : (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-950/50">
              <Check className="h-3 w-3" aria-hidden />
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm text-zinc-700 dark:text-zinc-200">
          {running && <span className="font-medium">正在取证 · </span>}
          <span className={running ? 'text-zinc-500 dark:text-zinc-400' : 'font-medium'}>{headline}</span>
        </span>
        <span className="shrink-0 text-xs tabular-nums text-zinc-400">
          {tools.length} 步{failed > 0 && <span className="text-red-500"> · {failed} 失败</span>}
        </span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-zinc-400 transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
      </button>

      {open && (
        <div className="border-t border-zinc-100 px-3.5 pb-3 pt-3 dark:border-zinc-800">
          {tools.length === 0 && running && <p className="pl-8 text-xs text-zinc-400">正在加载模型与工具…</p>}
          <ol>
            {tools.map((tool, index) => (
              <Step key={tool.id} tool={tool} last={index === tools.length - 1 && !draft} />
            ))}
          </ol>
          {draft && running && (
            <div className="flex gap-3">
              <span className="flex w-5 shrink-0 justify-center pt-1.5" aria-hidden>
                <span className="h-2 w-2 animate-pulse rounded-full bg-brand-500" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-zinc-500">正在撰写结论</p>
                <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-[13px] leading-relaxed text-zinc-500">
                  {visibleReport(draft)}
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function Step({ tool, last }: { tool: ToolActivity; last: boolean }) {
  const Icon = TOOL_ICONS[tool.name] ?? Wrench
  const state = !tool.done ? 'running' : tool.failed ? 'failed' : 'done'
  const iconClass =
    state === 'running'
      ? 'border-brand-200 bg-brand-50 text-brand-600 dark:border-brand-900 dark:bg-brand-950/60 dark:text-brand-300'
      : state === 'failed'
        ? 'border-red-200 bg-red-50 text-red-500 dark:border-red-900 dark:bg-red-950/50'
        : 'border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400'

  return (
    <li className={`flex gap-3 ${tool.subagent ? 'pl-6' : ''}`}>
      <div className="flex w-5 shrink-0 flex-col items-center">
        <span className={`flex h-5 w-5 items-center justify-center rounded-full border ${iconClass}`}>
          {state === 'running' ? (
            <Spinner className="h-2.5 w-2.5" />
          ) : state === 'failed' ? (
            <X className="h-3 w-3" aria-label="失败" />
          ) : (
            <Icon className="h-3 w-3" aria-hidden />
          )}
        </span>
        {!last && <span className="my-1 w-px flex-1 bg-zinc-200 dark:bg-zinc-800" aria-hidden />}
      </div>

      <div className={`min-w-0 flex-1 ${last ? '' : 'pb-3'}`}>
        {tool.note && (
          <p className="line-clamp-2 text-[13px] leading-5 text-zinc-700 dark:text-zinc-200" title={tool.note}>
            {tool.note}
          </p>
        )}
        <div
          className={`flex min-w-0 items-center gap-1.5 text-xs leading-5 ${tool.note ? 'mt-0.5 text-zinc-400' : 'text-zinc-500'}`}
        >
          <span className={`shrink-0 ${tool.note ? '' : 'font-medium text-zinc-700 dark:text-zinc-200'}`}>
            {tool.label || tool.name}
          </span>
          {tool.detail && (
            <code
              className="min-w-0 truncate rounded bg-zinc-100 px-1.5 font-mono text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
              title={tool.detail}
            >
              {shortTarget(tool.detail)}
            </code>
          )}
          {tool.summary && (
            <span className={`min-w-0 truncate ${tool.failed ? 'text-red-500' : ''}`} title={tool.summary}>
              {tool.summary}
            </span>
          )}
          {tool.seconds !== undefined && (
            <span className="ml-auto shrink-0 pl-2 tabular-nums text-zinc-400">{formatSeconds(tool.seconds)}</span>
          )}
        </div>
      </div>
    </li>
  )
}

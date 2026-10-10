import { memo, useDeferredValue, useMemo, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CITATION } from '../lib/format'
import { TIME_HREF, findTimes, remarkTimePoints, useTimeJump } from '../lib/time-jump'
import type { SourceTarget } from '../lib/types'

/** 深层路径只保留最后两级，完整路径放在 title 里 */
function shortCitation(source: string, full: string): string {
  const parts = source.split('/')
  if (parts.length <= 3) return full
  return `…/${parts.slice(-2).join('/')}${full.slice(source.length)}`
}

/**
 * 报告正文渲染。反引号里的 `app.log:42` / `app/order.py:10-20` 变成可点击的引用，
 * 点了在右侧原文面板打开（和终端里的 OSC 8 超链接是同一套写法）。
 * 不渲染原始 HTML：报告内容来自模型，可能夹带日志里的任意文本。
 */
export const Markdown = memo(function Markdown({ text, onOpen }: { text: string; onOpen?: (target: SourceTarget) => void }) {
  // 流式输出时每个字都会更新 text：解析让位给输入与滚动，跟不上时合并成下一帧再渲染，而不是逐字卡住主线程
  const deferred = useDeferredValue(text)
  // 只有能跳到原文的地方（工作区里的回答 / 报告）才把时间点变成跳转
  const onTime = useTimeJump()
  const jump = onOpen ? onTime : null
  const components = useMemo(() => buildComponents(onOpen, jump), [onOpen, jump])
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={jump ? TIME_PLUGINS : REMARK_PLUGINS} components={components}>
        {deferred}
      </ReactMarkdown>
    </div>
  )
})

const REMARK_PLUGINS = [remarkGfm]
const TIME_PLUGINS = [remarkGfm, remarkTimePoints]

const TIME_CLASS =
  'cursor-pointer rounded-sm text-inherit underline decoration-dotted decoration-brand-400 underline-offset-[3px] hover:bg-brand-50 hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-brand-500 dark:hover:bg-brand-950/50 dark:hover:text-brand-300'

function TimeLink({ at, onTime, children, mono }: { at: string; onTime: (at: string) => void; children: ReactNode; mono?: boolean }) {
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={() => onTime(at)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onTime(at)
        }
      }}
      className={`${TIME_CLASS} ${mono ? 'font-mono text-[0.85em]' : ''}`}
      title={`在时间线中定位 ${at.replace('T', ' ')}`}
    >
      {children}
    </span>
  )
}

function buildComponents(onOpen?: (target: SourceTarget) => void, onTime?: ((at: string) => void) | null): Components {
  return {
    code({ children, className }) {
      const value = String(children ?? '')
      if (!className && onTime) {
        const times = findTimes(value.trim())
        if (times.length === 1 && times[0].text === value.trim()) {
          return (
            <TimeLink at={times[0].value} onTime={onTime} mono>
              {value}
            </TimeLink>
          )
        }
      }
      const match = !className && onOpen ? CITATION.exec(value.trim()) : null
      if (match) {
        const start = Number(match[2])
        const end = match[3] ? Number(match[3]) : start
        const open = () => onOpen?.({ source: match[1], start, end })
        // 用行内 span 而不是 button：button 是行内块，长路径会被撑成一整块居中换行的文字
        return (
          <span
            role="button"
            tabIndex={0}
            onClick={open}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                open()
              }
            }}
            className="cursor-pointer rounded bg-brand-50 px-1 py-0.5 font-mono text-[0.85em] text-brand-700 underline decoration-brand-300 underline-offset-2 [box-decoration-break:clone] hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-500 dark:bg-brand-950/50 dark:text-brand-300 dark:hover:bg-brand-900/60"
            title={`在右侧查看原文：${value.trim()}`}
          >
            {shortCitation(match[1], value.trim())}
          </span>
        )
      }
      return <code className={className}>{children}</code>
    },
    a({ href, children }) {
      if (href?.startsWith(TIME_HREF)) {
        const at = href.slice(TIME_HREF.length)
        return onTime ? (
          <TimeLink at={at} onTime={onTime}>
            {children}
          </TimeLink>
        ) : (
          <>{children}</>
        )
      }
      return (
        <a href={href} target="_blank" rel="noreferrer noopener">
          {children}
        </a>
      )
    },
  }
}

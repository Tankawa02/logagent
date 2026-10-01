import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CITATION } from '../lib/format'
import type { SourceTarget } from '../lib/types'

/**
 * 报告正文渲染。反引号里的 `app.log:42` / `app/order.py:10-20` 变成可点击的引用，
 * 点了在右侧原文面板打开（和终端里的 OSC 8 超链接是同一套写法）。
 * 不渲染原始 HTML：报告内容来自模型，可能夹带日志里的任意文本。
 */
export function Markdown({ text, onOpen }: { text: string; onOpen?: (target: SourceTarget) => void }) {
  const components: Components = {
    code({ children, className }) {
      const value = String(children ?? '')
      const match = !className && onOpen ? CITATION.exec(value.trim()) : null
      if (match) {
        const start = Number(match[2])
        const end = match[3] ? Number(match[3]) : start
        return (
          <button
            type="button"
            onClick={() => onOpen?.({ source: match[1], start, end })}
            className="rounded bg-sky-50 px-1 py-0.5 font-mono text-[0.85em] text-sky-700 underline decoration-sky-300 underline-offset-2 hover:bg-sky-100 dark:bg-sky-950/50 dark:text-sky-300 dark:hover:bg-sky-900/60"
            title="在右侧查看原文"
          >
            {value}
          </button>
        )
      }
      return <code className={className}>{children}</code>
    },
    a({ href, children }) {
      return (
        <a href={href} target="_blank" rel="noreferrer noopener">
          {children}
        </a>
      )
    },
  }
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
}

import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CITATION } from '../lib/format'
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
export function Markdown({ text, onOpen }: { text: string; onOpen?: (target: SourceTarget) => void }) {
  const components: Components = {
    code({ children, className }) {
      const value = String(children ?? '')
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

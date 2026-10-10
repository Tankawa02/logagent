import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { describe, expect, it } from 'vitest'
import { splitBlocks } from './markdown-blocks'
import { nextLength } from './smooth-text'

const render = (text: string) => renderToStaticMarkup(createElement(ReactMarkdown, { remarkPlugins: [remarkGfm] }, text))
const renderBlocks = (text: string) => splitBlocks(text).map(render).join('')
/** 块之间的换行只是空白文本，不影响显示 */
const normalize = (html: string) => html.replace(/>\s+</g, '><').trim()

const SAMPLES: Record<string, string> = {
  paragraphs: '第一段 `app.log:42`。\n\n第二段 **加粗**。\n\n\n第三段',
  looseList: '- 第一项\n\n- 第二项\n\n  续行段落\n\n- 第三项\n\n之后的正文',
  orderedList: '1. 一\n\n2. 二\n\n3. 三',
  fenceWithBlankLines: '前文\n\n```python\ndef f():\n\n    return 1\n\n\n```\n\n后文',
  tildeFence: '~~~\n```\n\n不是结束\n~~~\n\n后文',
  table: '| 时间 | 错误 |\n|---|---|\n| 14:00 | 120 |\n\n总结',
  blockquote: '> 引用一\n\n> 引用二\n\n正文',
  headings: '## 结论\n\n内容\n\n### 证据\n\n- a\n- b',
  indentedCode: '段落\n\n    缩进代码\n\n    还是代码\n\n结束',
}

describe('splitBlocks', () => {
  it.each(Object.entries(SAMPLES))('按块渲染与整篇渲染一致：%s', (_name, text) => {
    expect(normalize(renderBlocks(text))).toBe(normalize(render(text)))
  })

  it('在顶层空行处切开，前面的块在继续输出时保持不变', () => {
    const partial = '第一段\n\n第二段\n\n第三'
    const more = `${partial}段继续写`
    const a = splitBlocks(partial)
    const b = splitBlocks(more)
    expect(a).toHaveLength(3)
    expect(b.slice(0, 2)).toEqual(a.slice(0, 2))
  })

  it('未闭合的代码块不会被切开', () => {
    expect(splitBlocks('前文\n\n```\na\n\nb')).toHaveLength(2)
  })

  it('同一个列表的相邻项留在一块', () => {
    expect(splitBlocks('- a\n\n- b\n\n- c')).toHaveLength(1)
  })

  it('有引用式定义时整篇一起解析', () => {
    expect(splitBlocks('见 [文档][1]\n\n其它\n\n[1]: https://example.com')).toHaveLength(1)
  })
})

describe('nextLength', () => {
  it('至少前进一小步，积压越多走得越快', () => {
    const text = 'x'.repeat(600)
    expect(nextLength(text, 598)).toBe(600)
    expect(nextLength(text, 0)).toBe(100)
  })

  it('积压再多也能在一秒内（60 帧）追上', () => {
    const text = 'x'.repeat(20_000)
    let shown = 0
    let frames = 0
    while (shown < text.length) {
      shown = nextLength(text, shown)
      frames++
    }
    expect(frames).toBeLessThan(60)
  })

  it('不会切开 emoji 的代理对', () => {
    const text = 'ab😀cd'
    for (let from = 0; from < text.length; from = nextLength(text, from)) {
      const cut = nextLength(text, from)
      const code = text.charCodeAt(cut - 1)
      expect(code >= 0xd800 && code <= 0xdbff).toBe(false)
    }
  })

  it('已经显示完时保持不变', () => {
    expect(nextLength('abc', 3)).toBe(3)
  })
})

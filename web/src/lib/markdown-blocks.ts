const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/
const LIST_ITEM = /^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:\s|$)/
/** 引用式链接 / 脚注的定义可能被别的块引用，拆开会断链：遇到就整段一起解析 */
const DEFINITION = /^ {0,3}\[\^?[^\]]+\]:\s/m

/**
 * 把 Markdown 按顶层空行切成互不影响的块，每块单独解析、单独缓存。
 * 流式输出时前面的块不再变化，只有最后一块需要重新解析，
 * 长回答每个字的解析开销从「整篇」降到「一段」。
 *
 * 只在确定安全的地方切：围栏代码块内部、缩进的续行、同一个列表的相邻项都不切，
 * 渲染结果和整篇一起解析一致。
 */
export function splitBlocks(text: string): string[] {
  if (DEFINITION.test(text)) return [text]
  const lines = text.split('\n')
  const blocks: string[] = []
  let start = 0
  let fence: { char: string; size: number } | null = null
  let hasContent = false
  let blockIsList = false
  let afterBlank = false

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (fence) {
      const close = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(line)
      if (close && close[1][0] === fence.char && close[1].length >= fence.size) fence = null
      continue
    }
    if (line.trim() === '') {
      if (hasContent) afterBlank = true
      continue
    }
    const isList = LIST_ITEM.test(line)
    if (afterBlank && !/^\s/.test(line) && !(blockIsList && isList)) {
      blocks.push(lines.slice(start, i).join('\n'))
      start = i
      hasContent = false
    }
    if (!hasContent) {
      hasContent = true
      blockIsList = isList
    }
    afterBlank = false
    const open = FENCE_OPEN.exec(line)
    if (open) fence = { char: open[1][0], size: open[1].length }
  }
  blocks.push(lines.slice(start).join('\n'))
  return blocks
}

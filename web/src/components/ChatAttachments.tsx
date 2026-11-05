import { FileText, ImageIcon, X } from 'lucide-react'
import { useState } from 'react'
import { formatSize, type DisplayAttachment, type PendingAttachment } from '../lib/attachments'

/** 输入框上方：已选好、还没发出的附件，可以逐个移除 */
export function AttachmentTray({ items, onRemove }: { items: PendingAttachment[]; onRemove: (id: string) => void }) {
  if (items.length === 0) return null
  return (
    <ul aria-label="待发送的附件" className="flex flex-wrap gap-2 px-2 pb-1 pt-1">
      {items.map((item) => (
        <li key={item.id} className="group relative">
          {item.kind === 'image' && item.preview ? (
            <div className="h-16 w-16 overflow-hidden rounded-xl bg-zinc-100 ring-1 ring-inset ring-zinc-200 dark:bg-zinc-800 dark:ring-zinc-700">
              <img src={item.preview} alt={item.name} className="h-full w-full object-cover" />
            </div>
          ) : (
            <div className="flex h-16 max-w-56 items-center gap-2.5 rounded-xl bg-zinc-50 pl-2.5 pr-8 ring-1 ring-inset ring-zinc-200 dark:bg-zinc-800/60 dark:ring-zinc-700">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-950/50 dark:text-brand-300">
                <FileText className="h-4 w-4" aria-hidden />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium text-zinc-800 dark:text-zinc-100" title={item.name}>
                  {item.name}
                </span>
                <span className="block text-xs text-zinc-500 dark:text-zinc-400">{formatSize(item.size)}</span>
              </span>
            </div>
          )}
          <button
            type="button"
            onClick={() => onRemove(item.id)}
            aria-label={`移除附件 ${item.name}`}
            title="移除"
            className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-zinc-900 text-white shadow-sm ring-2 ring-white transition-opacity hover:bg-zinc-700 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100 dark:bg-zinc-100 dark:text-zinc-900 dark:ring-zinc-900"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </li>
      ))}
    </ul>
  )
}

/** 问题气泡上方：这一问带了哪些附件。历史轮次只存了文件名，没有图片原图 */
export function AttachmentList({ items }: { items: DisplayAttachment[] }) {
  const [expanded, setExpanded] = useState<number | null>(null)
  if (items.length === 0) return null
  return (
    <ul aria-label="附件" className="flex max-w-[85%] flex-wrap justify-end gap-2">
      {items.map((item, index) =>
        item.kind === 'image' && item.preview ? (
          <li key={`${item.name}-${index}`} className={expanded === index ? 'w-full' : undefined}>
            <button
              type="button"
              onClick={() => setExpanded(expanded === index ? null : index)}
              aria-expanded={expanded === index}
              title={expanded === index ? '收起' : '点击放大'}
              className="block overflow-hidden rounded-xl ring-1 ring-inset ring-zinc-200 transition-shadow hover:ring-brand-300 dark:ring-zinc-700"
            >
              <img
                src={item.preview}
                alt={item.name}
                className={expanded === index ? 'max-h-[70vh] w-full object-contain' : 'h-28 max-w-60 object-cover'}
              />
            </button>
          </li>
        ) : (
          <li
            key={`${item.name}-${index}`}
            className="flex max-w-64 items-center gap-2 rounded-xl bg-white py-1.5 pl-2 pr-3 ring-1 ring-inset ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-700"
          >
            {item.kind === 'image' ? (
              <ImageIcon className="h-4 w-4 shrink-0 text-brand-600" aria-hidden />
            ) : (
              <FileText className="h-4 w-4 shrink-0 text-brand-600" aria-hidden />
            )}
            <span className="min-w-0 truncate text-xs font-medium text-zinc-800 dark:text-zinc-100" title={item.name}>
              {item.name}
            </span>
            <span className="shrink-0 text-xs text-zinc-500 dark:text-zinc-400">
              {formatSize(item.size)}
              {item.truncated ? ' · 已截断' : ''}
            </span>
          </li>
        ),
      )}
    </ul>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useRef, useState } from 'react'
import { api } from '../lib/api'
import { useCopy, useModal } from '../lib/hooks'
import type { Meta, Share } from '../lib/types'
import { useToast } from './Feedback'
import { Select } from './Select'
import { Button, ErrorBox, Spinner } from './ui'

const TTL_OPTIONS: { label: string; hours: number | null }[] = [
  { label: '1 小时', hours: 1 },
  { label: '1 天', hours: 24 },
  { label: '7 天', hours: 24 * 7 },
  { label: '30 天', hours: 24 * 30 },
  { label: '永久（直到撤销）', hours: null },
]

export function SharePanel({ session, meta, onClose }: { session: string; meta: Meta; onClose: () => void }) {
  const client = useQueryClient()
  const [ttl, setTtl] = useState<number | null>(24 * 7)
  const [created, setCreated] = useState<Share | null>(null)
  const { state: copyState, copy } = useCopy()
  const dialog = useRef<HTMLDivElement>(null)
  const onKeyDown = useModal(dialog, onClose)
  const toast = useToast()
  const shares = useQuery({ queryKey: ['shares', session], queryFn: () => api.shares(session) })

  const create = useMutation({
    mutationFn: () => api.createShare(session, ttl),
    onSuccess: (share) => {
      setCreated(share)
      void client.invalidateQueries({ queryKey: ['shares', session] })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeShare(session, id),
    onSuccess: (_, id) => {
      if (created?.id === id) setCreated(null)
      void client.invalidateQueries({ queryKey: ['shares', session] })
      toast('已撤销分享链接，同事将无法再打开')
    },
  })

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 p-4 pt-[10vh]" onClick={onClose}>
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-title"
        aria-describedby="share-desc"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="w-full max-w-lg space-y-4 rounded-lg border border-zinc-200 bg-white p-5 shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="share-title" className="text-base font-semibold">
              分享给同事
            </h2>
            <p id="share-desc" className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              生成只读链接：能看报告、错误时间线、证据原文和导出，内容始终脱敏，不能续问、不能看其他会话。
            </p>
          </div>
          <Button variant="ghost" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>

        {meta.loopback && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
            当前服务只监听本机，同事打不开这个地址。要交接给别人，请用 <code>log-agent serve --host 0.0.0.0</code>{' '}
            启动，或经反向代理访问并设置 <code>--public-url</code>。
          </div>
        )}

        <div className="flex flex-wrap items-end gap-2">
          <div className="text-sm">
            <label htmlFor="share-ttl" className="mb-1 block text-xs text-zinc-500">
              有效期
            </label>
            <Select
              id="share-ttl"
              value={ttl === null ? 'never' : String(ttl)}
              onChange={(v) => setTtl(v === 'never' ? null : Number(v))}
              options={TTL_OPTIONS.map((o) => ({ value: o.hours === null ? 'never' : String(o.hours), label: o.label }))}
              className="min-w-32"
            />
          </div>
          <Button variant="primary" onClick={() => create.mutate()} disabled={create.isPending}>
            {create.isPending && <Spinner />}生成链接
          </Button>
        </div>
        {create.error && <ErrorBox error={create.error} />}

        {created?.url && (
          <div className="space-y-1.5">
            <p className="text-xs text-zinc-500">链接只显示这一次，请现在复制：</p>
            <div className="flex gap-2">
              <input
                readOnly
                value={created.url}
                onFocus={(e) => e.target.select()}
                className="min-w-0 flex-1 rounded-md border border-zinc-300 bg-zinc-50 px-2 py-1.5 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-950"
              />
              <Button onClick={() => void copy(created.url!)}>
                {copyState === 'copied' ? '已复制' : copyState === 'failed' ? '复制失败，请手动复制' : '复制'}
              </Button>
            </div>
          </div>
        )}

        <div>
          <h3 className="mb-1.5 text-xs font-semibold text-zinc-500">已生成的链接</h3>
          {shares.isLoading && <Spinner />}
          {shares.data && shares.data.length === 0 && <p className="text-sm text-zinc-400">暂无</p>}
          <ul className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
            {shares.data?.map((share) => (
              <li key={share.id} className="flex items-center justify-between gap-2 py-1.5">
                <span className="font-mono text-xs text-zinc-600 dark:text-zinc-300">…/s/…{share.hint}</span>
                <span className="text-xs text-zinc-500">
                  {share.created_at} 创建 · {share.expires_at ? `${share.expires_at} 过期` : '永久'}
                </span>
                <Button variant="ghost" className="text-xs text-red-600" onClick={() => revoke.mutate(share.id)}>
                  撤销
                </Button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import type { Meta, Share } from '../lib/types'
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
  const [copied, setCopied] = useState(false)
  const shares = useQuery({ queryKey: ['shares', session], queryFn: () => api.shares(session) })

  const create = useMutation({
    mutationFn: () => api.createShare(session, ttl),
    onSuccess: (share) => {
      setCreated(share)
      setCopied(false)
      void client.invalidateQueries({ queryKey: ['shares', session] })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeShare(session, id),
    onSuccess: (_, id) => {
      if (created?.id === id) setCreated(null)
      void client.invalidateQueries({ queryKey: ['shares', session] })
    },
  })

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 p-4 pt-[10vh]" onClick={onClose}>
      <div
        role="dialog"
        aria-label="分享会话"
        className="w-full max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-5 shadow-xl dark:border-gray-800 dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold">分享给同事</h2>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              生成只读链接：能看报告、错误时间线、证据原文和导出，内容始终脱敏，不能续问、不能看其他会话。
            </p>
          </div>
          <Button variant="ghost" onClick={onClose} aria-label="关闭">
            ✕
          </Button>
        </div>

        {meta.loopback && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
            当前服务只监听本机，同事打不开这个地址。要交接给别人，请用 <code>log-agent serve --host 0.0.0.0</code>{' '}
            启动，或经反向代理访问并设置 <code>--public-url</code>。
          </div>
        )}

        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-gray-500">有效期</span>
            <select
              value={ttl === null ? 'never' : String(ttl)}
              onChange={(e) => setTtl(e.target.value === 'never' ? null : Number(e.target.value))}
              className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-950"
            >
              {TTL_OPTIONS.map((o) => (
                <option key={o.label} value={o.hours === null ? 'never' : String(o.hours)}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <Button variant="primary" onClick={() => create.mutate()} disabled={create.isPending}>
            {create.isPending && <Spinner />}生成链接
          </Button>
        </div>
        {create.error && <ErrorBox error={create.error} />}

        {created?.url && (
          <div className="space-y-1.5">
            <p className="text-xs text-gray-500">链接只显示这一次，请现在复制：</p>
            <div className="flex gap-2">
              <input
                readOnly
                value={created.url}
                onFocus={(e) => e.target.select()}
                className="min-w-0 flex-1 rounded-md border border-gray-300 bg-gray-50 px-2 py-1.5 font-mono text-xs dark:border-gray-700 dark:bg-gray-950"
              />
              <Button
                onClick={() => {
                  void navigator.clipboard?.writeText(created.url!).then(() => setCopied(true))
                }}
              >
                {copied ? '已复制' : '复制'}
              </Button>
            </div>
          </div>
        )}

        <div>
          <h3 className="mb-1.5 text-xs font-semibold text-gray-500">已生成的链接</h3>
          {shares.isLoading && <Spinner />}
          {shares.data && shares.data.length === 0 && <p className="text-sm text-gray-400">暂无</p>}
          <ul className="divide-y divide-gray-100 text-sm dark:divide-gray-800">
            {shares.data?.map((share) => (
              <li key={share.id} className="flex items-center justify-between gap-2 py-1.5">
                <span className="font-mono text-xs text-gray-600 dark:text-gray-300">…/s/…{share.hint}</span>
                <span className="text-xs text-gray-500">
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

import { useQuery } from '@tanstack/react-query'
import { WifiOff } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { api } from '../lib/api'

function subscribeOnline(listener: () => void) {
  window.addEventListener('online', listener)
  window.addEventListener('offline', listener)
  return () => {
    window.removeEventListener('online', listener)
    window.removeEventListener('offline', listener)
  }
}

/**
 * 侧边栏底部的小圆点太不显眼：已经连上过的后端之后探测失败（或浏览器断网）时，
 * 在主区顶部给一条明确的提示，恢复后自动消失。
 */
export function ConnectionBanner() {
  const online = useSyncExternalStore(subscribeOnline, () => navigator.onLine)
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta, refetchInterval: 30_000, refetchIntervalInBackground: false })
  const lost = !online || (meta.isRefetchError && !!meta.data)
  if (!lost) return null

  return (
    <div
      role="status"
      className="flex animate-banner-in items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200"
    >
      <WifiOff className="h-4 w-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">{online ? '与 log-agent 服务的连接已断开，正在重试…' : '网络已断开，恢复后会自动重新连接。'}</span>
      {online && (
        <button
          type="button"
          onClick={() => void meta.refetch()}
          disabled={meta.isFetching}
          className="shrink-0 rounded-md px-2 py-0.5 text-sm font-medium text-amber-900 underline-offset-2 hover:underline disabled:opacity-60 dark:text-amber-200"
        >
          {meta.isFetching ? '重试中…' : '立即重试'}
        </button>
      )}
    </div>
  )
}

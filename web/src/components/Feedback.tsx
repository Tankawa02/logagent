import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useModal } from '../lib/hooks'
import { Button } from './ui'

type ConfirmOptions = {
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}
type ToastTone = 'success' | 'error' | 'info'
type ToastItem = { id: number; message: string; tone: ToastTone }

const ConfirmContext = createContext<((options: ConfirmOptions) => Promise<boolean>) | null>(null)
const ToastContext = createContext<((message: string, tone?: ToastTone) => void) | null>(null)

/** 应用内确认框：返回 Promise<boolean>，用法与 window.confirm 一致但风格统一、可标红危险操作 */
export function useConfirm() {
  const confirm = useContext(ConfirmContext)
  if (!confirm) throw new Error('useConfirm 必须在 FeedbackProvider 内使用')
  return confirm
}

/** 轻提示：右下角短暂出现，读屏通过 aria-live 播报 */
export function useToast() {
  const toast = useContext(ToastContext)
  if (!toast) throw new Error('useToast 必须在 FeedbackProvider 内使用')
  return toast
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null)
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(0)

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setPending((prev) => {
          prev?.resolve(false)
          return { ...options, resolve }
        })
      }),
    [],
  )

  const settle = useCallback((ok: boolean) => {
    setPending((prev) => {
      prev?.resolve(ok)
      return null
    })
  }, [])

  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((t) => t.id !== id)), [])

  const toast = useCallback((message: string, tone: ToastTone = 'success') => {
    const id = ++nextId.current
    setToasts((list) => [...list.slice(-2), { id, message, tone }])
  }, [])

  return (
    <ConfirmContext.Provider value={confirm}>
      <ToastContext.Provider value={toast}>
        {children}
        {pending && <ConfirmDialog options={pending} onSettle={settle} />}
        <div
          aria-live="polite"
          className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-center gap-2 sm:inset-x-auto sm:right-5 sm:items-end"
        >
          {toasts.map((t) => (
            <Toast key={t.id} item={t} onDismiss={dismiss} />
          ))}
        </div>
      </ToastContext.Provider>
    </ConfirmContext.Provider>
  )
}

function ConfirmDialog({ options, onSettle }: { options: ConfirmOptions; onSettle: (ok: boolean) => void }) {
  const dialog = useRef<HTMLDivElement>(null)
  const cancel = useCallback(() => onSettle(false), [onSettle])
  const onKeyDown = useModal(dialog, cancel)
  const { title, description, confirmLabel = '确定', cancelLabel = '取消', danger = false } = options

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[18vh]" onClick={cancel}>
      <div
        ref={dialog}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={description ? 'confirm-desc' : undefined}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm animate-dialog-in rounded-xl border border-zinc-200 bg-white p-5 shadow-xl motion-reduce:animate-none dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div className="flex gap-3">
          {danger && (
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-50 text-red-600 dark:bg-red-950/50 dark:text-red-400">
              <AlertTriangle className="h-4 w-4" aria-hidden />
            </span>
          )}
          <div className="min-w-0 space-y-1.5">
            <h2 id="confirm-title" className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
              {title}
            </h2>
            {description && (
              <div id="confirm-desc" className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
                {description}
              </div>
            )}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          {/* 危险操作默认聚焦「取消」，避免回车误删 */}
          <Button onClick={cancel} data-autofocus={danger ? '' : undefined}>
            {cancelLabel}
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            onClick={() => onSettle(true)}
            data-autofocus={danger ? undefined : ''}
            className={
              danger
                ? 'border-red-600 bg-red-600 text-white hover:bg-red-700 dark:border-red-600 dark:bg-red-600 dark:text-white dark:hover:bg-red-500'
                : ''
            }
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

const TOAST_STYLE: Record<ToastTone, { icon: typeof Info; className: string }> = {
  success: { icon: CheckCircle2, className: 'text-emerald-600 dark:text-emerald-400' },
  error: { icon: XCircle, className: 'text-red-600 dark:text-red-400' },
  info: { icon: Info, className: 'text-brand-600 dark:text-brand-400' },
}

function Toast({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const [paused, setPaused] = useState(false)
  useEffect(() => {
    if (paused) return
    const timer = setTimeout(() => onDismiss(item.id), item.tone === 'error' ? 5000 : 2500)
    return () => clearTimeout(timer)
  }, [item, onDismiss, paused])
  const { icon: Icon, className } = useMemo(() => TOAST_STYLE[item.tone], [item.tone])

  return (
    <div
      role={item.tone === 'error' ? 'alert' : 'status'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className="pointer-events-auto flex max-w-sm items-center gap-2.5 rounded-xl border border-zinc-200 bg-white py-2 pl-3 pr-1.5 text-sm text-zinc-800 shadow-lg animate-toast-in motion-reduce:animate-none dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100"
    >
      <Icon className={`h-4 w-4 shrink-0 ${className}`} aria-hidden />
      <span className="min-w-0 flex-1">{item.message}</span>
      <button
        type="button"
        onClick={() => onDismiss(item.id)}
        aria-label="关闭提示"
        className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  )
}

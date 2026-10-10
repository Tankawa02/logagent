import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Download, ThumbsDown, ThumbsUp, TriangleAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../lib/api'
import type { FeedbackRating, TurnFeedback } from '../lib/types'
import { useToast } from './Feedback'

const OPTIONS: { rating: FeedbackRating; label: string; icon: typeof ThumbsUp }[] = [
  { rating: 'up', label: '有用', icon: ThumbsUp },
  { rating: 'down', label: '没用', icon: ThumbsDown },
  { rating: 'wrong', label: '根因不对', icon: TriangleAlert },
]

/** 本人视图里每轮报告底部的反馈：有用 / 没用 / 根因不对（附纠正）。纠正会作为历史案例线索喂给之后的同类排查。 */
export function TurnFeedbackBar({
  session,
  turn,
  feedback,
  onSaved,
}: {
  session: string
  turn: number
  feedback: TurnFeedback | null | undefined
  onSaved: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [comment, setComment] = useState(feedback?.comment ?? '')

  const save = useMutation({
    mutationFn: ({ rating, text }: { rating: FeedbackRating | null; text: string }) =>
      rating ? api.setFeedback(session, turn, rating, text) : api.clearFeedback(session, turn),
    onSuccess: (_data, { rating }) => {
      setEditing(false)
      onSaved()
      void queryClient.invalidateQueries({ queryKey: ['feedback'] })
      if (rating === 'wrong') toast('已记录纠正，之后的同类排查会参考它', 'success')
    },
    onError: (error) => toast(`反馈保存失败：${String(error)}`, 'error'),
  })

  const pick = (rating: FeedbackRating) => {
    if (feedback?.rating === rating && rating !== 'wrong') {
      save.mutate({ rating: null, text: '' })
      return
    }
    if (rating === 'wrong') {
      setComment(feedback?.rating === 'wrong' ? feedback.comment : '')
      setEditing(true)
      return
    }
    save.mutate({ rating, text: '' })
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    save.mutate({ rating: 'wrong', text: comment })
  }

  return (
    <div className="space-y-2 border-t border-zinc-200/70 px-4 py-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-zinc-500">这一轮的结论</span>
        {OPTIONS.map(({ rating, label, icon: Icon }) => {
          const active = feedback?.rating === rating
          return (
            <button
              key={rating}
              type="button"
              aria-pressed={active}
              disabled={save.isPending}
              onClick={() => pick(rating)}
              className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs ring-1 ring-inset transition-colors disabled:opacity-60 ${
                active
                  ? rating === 'up'
                    ? 'bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-900'
                    : 'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900'
                  : 'bg-white text-zinc-600 ring-zinc-200 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-700 dark:hover:bg-zinc-800'
              }`}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
              {label}
            </button>
          )
        })}
        <a
          href={api.evalCaseUrl(session, turn)}
          className="ml-auto inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
          title="导出为评测案例（case.toml），放进 evals/cases/ 后可用 log-agent eval run 回归"
        >
          <Download className="h-3.5 w-3.5" aria-hidden />
          导出评测案例
        </a>
      </div>
      {feedback?.rating === 'wrong' && feedback.comment && !editing && (
        <p className="text-xs text-zinc-600 dark:text-zinc-400">
          纠正：{feedback.comment}{' '}
          <button type="button" className="underline underline-offset-2" onClick={() => setEditing(true)}>
            修改
          </button>
        </p>
      )}
      {editing && (
        <form onSubmit={submit} className="space-y-2">
          <label htmlFor={`fb-${turn}`} className="sr-only">
            真正的根因是什么
          </label>
          <textarea
            id={`fb-${turn}`}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="真正的根因是什么？会作为历史案例提供给之后的同类排查（可留空）"
            className="w-full resize-y rounded-md border border-zinc-200 bg-white px-2.5 py-1.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:focus:ring-brand-900"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="rounded-md px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={save.isPending}
              className="rounded-md bg-zinc-900 px-2.5 py-1 text-xs font-medium text-white hover:bg-zinc-800 disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
            >
              保存纠正
            </button>
          </div>
        </form>
      )}
    </div>
  )
}

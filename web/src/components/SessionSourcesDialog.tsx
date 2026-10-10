import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { api } from '../lib/api'
import { useModal } from '../lib/hooks'
import { checkBound, checkRange, type BoundCheck } from '../lib/time-input'
import type { SessionDetail } from '../lib/types'
import { useToast } from './Feedback'
import { SourcePicker, type SourceValidity } from './SourcePicker'
import { TimeField, TimePresets } from './TimeField'
import { Button, ErrorBox, Spinner } from './ui'

const NO_ISSUES: SourceValidity = { invalid: 0, checking: 0 }

/** 会话中途追加 / 移除日志与源码、调整时间窗口和基线（对应 CLI 的 /add-log、/remove-log、/window、/baseline） */
export function SessionSourcesDialog({ info, onClose }: { info: SessionDetail; onClose: () => void }) {
  const client = useQueryClient()
  const toast = useToast()
  const dialog = useRef<HTMLFormElement>(null)
  const onKeyDown = useModal(dialog, onClose)
  const settings = info.settings as Record<string, string | null>
  const [logs, setLogs] = useState(() => info.logs.map((l) => l.path))
  const [code, setCode] = useState(() => info.code.map((c) => c.path))
  const [since, setSince] = useState(settings.since ?? '')
  const [until, setUntil] = useState(settings.until ?? '')
  const [baseline, setBaseline] = useState(settings.baseline ?? '')
  const [logValidity, setLogValidity] = useState(NO_ISSUES)
  const [codeValidity, setCodeValidity] = useState(NO_ISSUES)
  const timezone = settings.timezone || 'UTC'

  const save = useMutation({
    mutationFn: () =>
      api.updateSources(info.name, {
        logs,
        code,
        since: since.trim() || undefined,
        until: until.trim() || undefined,
        baseline: baseline.trim() || undefined,
      }),
    onSuccess: (updated) => {
      void client.invalidateQueries({ queryKey: ['owner', info.name, 'session'] })
      void client.invalidateQueries({ queryKey: ['owner', info.name, 'timeline'] })
      void client.invalidateQueries({ queryKey: ['sessions'] })
      toast(updated.pending_change ? '已更新，下一次提问时会告知 agent；已生成的报告保持原设置' : '已更新来源与范围')
      onClose()
    },
  })

  const sinceCheck = checkBound(since, false)
  const untilRaw = checkBound(until, true)
  const untilCheck: BoundCheck | null =
    untilRaw?.ok && sinceCheck?.ok && sinceCheck.order !== null && untilRaw.order !== null && untilRaw.order < sinceCheck.order
      ? { ok: false, error: '结束时间早于开始时间' }
      : untilRaw
  const baselineCheck = checkRange(baseline)
  const blocker =
    logs.length === 0
      ? '至少保留一个日志文件'
      : logValidity.invalid + codeValidity.invalid > 0
        ? '有路径不存在或类型不对'
        : logValidity.checking + codeValidity.checking > 0
          ? '正在检查路径…'
          : [sinceCheck, untilCheck, baselineCheck].some((c) => c && !c.ok)
            ? '时间设置有误'
            : null

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!blocker && !save.isPending) save.mutate()
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-auto bg-black/30 p-4 pt-[8vh]" onClick={onClose}>
      <form
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="sources-title"
        aria-describedby="sources-desc"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        onSubmit={onSubmit}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-2xl space-y-5 rounded-2xl border border-zinc-200 bg-white p-5 shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="sources-title" className="text-base font-semibold text-zinc-900 dark:text-zinc-50">
              调整来源与范围
            </h2>
            <p id="sources-desc" className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              {info.turns > 0
                ? '下一次提问生效，会提醒 agent 按新的来源和范围重新核实；已生成的报告保持原设置。'
                : '第一次提问时按这里的设置开始分析。'}
            </p>
          </div>
          <Button variant="ghost" type="button" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>

        <div className="space-y-4 rounded-xl bg-zinc-50/70 p-4 dark:bg-zinc-950/40">
          <SourcePicker kind="log" values={logs} onChange={setLogs} onValidity={setLogValidity} />
          <SourcePicker kind="code" values={code} onChange={setCode} onValidity={setCodeValidity} />
        </div>

        <fieldset className="grid gap-3 sm:grid-cols-2">
          <legend className="mb-2 text-xs font-medium text-zinc-700 dark:text-zinc-300">
            时间范围 <span className="font-normal text-zinc-500 dark:text-zinc-400">· 按 {timezone} 解释</span>
          </legend>
          <TimePresets
            timezone={timezone}
            active={!!(since || until)}
            onPick={(s, u) => {
              setSince(s)
              setUntil(u)
            }}
            onClear={() => {
              setSince('')
              setUntil('')
            }}
          />
          <TimeField
            id="edit-since"
            label="开始时间"
            value={since}
            onChange={setSince}
            placeholder="不填表示从日志开头"
            check={sinceCheck}
          />
          <TimeField
            id="edit-until"
            label="结束时间"
            value={until}
            onChange={setUntil}
            placeholder="不填表示到日志结尾"
            check={untilCheck}
          />
          <TimeField
            id="edit-baseline"
            label="基线时段"
            value={baseline}
            onChange={setBaseline}
            placeholder="用于对比的正常时段，如 13:00~13:30；留空表示不对比"
            check={baselineCheck}
            wide
          />
        </fieldset>

        {save.error && <ErrorBox error={save.error} />}

        <div className="flex items-center justify-end gap-2">
          {blocker && <span className="mr-auto text-xs text-zinc-500 dark:text-zinc-400">{blocker}</span>}
          <Button variant="ghost" type="button" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" variant="primary" disabled={!!blocker || save.isPending}>
            {save.isPending && <Spinner className="border-white/40 border-t-white" />}
            保存
          </Button>
        </div>
      </form>
    </div>
  )
}

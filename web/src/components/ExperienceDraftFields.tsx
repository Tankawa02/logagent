import { MEMORY_FIELD } from './MemoryCandidate'

export type ExperienceDraft = { title: string; trigger: string; strategy: string }

const LABEL = 'mb-1.5 block text-[13px] font-medium text-foreground'

export function ExperienceDraftFields({
  draft,
  onChange,
  idPrefix,
}: {
  draft: ExperienceDraft
  onChange: (d: ExperienceDraft) => void
  idPrefix: string
}) {
  return (
    <div className="space-y-3">
      <div>
        <label htmlFor={`${idPrefix}-title`} className={LABEL}>
          标题
        </label>
        <input
          id={`${idPrefix}-title`}
          value={draft.title}
          onChange={(e) => onChange({ ...draft, title: e.target.value })}
          maxLength={60}
          placeholder="一句话说清做法，如：502 激增先查连接池"
          className={`${MEMORY_FIELD} h-9 py-0`}
        />
      </div>
      <div>
        <label htmlFor={`${idPrefix}-trigger`} className={LABEL}>
          什么情况下适用
        </label>
        <input
          id={`${idPrefix}-trigger`}
          value={draft.trigger}
          onChange={(e) => onChange({ ...draft, trigger: e.target.value })}
          maxLength={200}
          placeholder="如：Nginx 502/504 突增；对任何排查都适用时写“通用”"
          className={`${MEMORY_FIELD} h-9 py-0`}
        />
      </div>
      <div>
        <label htmlFor={`${idPrefix}-strategy`} className={LABEL}>
          排查策略
        </label>
        <textarea
          id={`${idPrefix}-strategy`}
          value={draft.strategy}
          onChange={(e) => onChange({ ...draft, strategy: e.target.value })}
          rows={3}
          maxLength={400}
          placeholder="按顺序写出先查什么、再查什么"
          className={`${MEMORY_FIELD} resize-none`}
        />
      </div>
    </div>
  )
}

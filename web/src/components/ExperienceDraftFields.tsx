import { MEMORY_FIELD } from './MemoryCandidate'

export type ExperienceDraft = { title: string; trigger: string; strategy: string }

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
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-title`} className="text-xs font-medium text-muted-foreground">
          标题
        </label>
        <input
          id={`${idPrefix}-title`}
          value={draft.title}
          onChange={(e) => onChange({ ...draft, title: e.target.value })}
          maxLength={60}
          placeholder="例如：连接池耗尽先核对借出与归还"
          className={MEMORY_FIELD}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-trigger`} className="text-xs font-medium text-muted-foreground">
          适用条件
        </label>
        <input
          id={`${idPrefix}-trigger`}
          value={draft.trigger}
          onChange={(e) => onChange({ ...draft, trigger: e.target.value })}
          maxLength={200}
          placeholder="什么样的日志特征 / 错误类型 / 技术栈；对任何排查都适用时写“通用”"
          className={MEMORY_FIELD}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-strategy`} className="text-xs font-medium text-muted-foreground">
          做法
        </label>
        <textarea
          id={`${idPrefix}-strategy`}
          value={draft.strategy}
          onChange={(e) => onChange({ ...draft, strategy: e.target.value })}
          rows={3}
          maxLength={400}
          placeholder="先查什么、用什么工具或搜索词、怎么判断"
          className={MEMORY_FIELD}
        />
      </div>
    </div>
  )
}

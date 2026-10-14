import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CircleAlert, Eye, EyeOff, Lock, PlugZap } from 'lucide-react'
import { useId, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { isUnknownModel, ModelCombobox } from '../components/ModelCombobox'
import { Badge, Button, ErrorBox, FormSkeleton, Spinner } from '../components/ui'
import { PageScaffold } from '../components/TopBar'
import { api } from '../lib/api'
import type { ConnectionTestResult, SettingField, SettingKey, Settings, SettingsUpdate, SettingValue } from '../lib/types'

const FIELD =
  'h-9 w-full rounded-lg border border-input bg-background px-3 text-[13px] text-foreground outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground aria-[invalid=true]:border-red-400'

const SECTIONS = [
  { id: 'connection', label: '模型连接' },
  { id: 'tiering', label: '模型分层' },
  { id: 'request', label: '请求' },
  { id: 'defaults', label: '分析默认值' },
  { id: 'experience', label: '排查经验' },
] as const

const KEYS: SettingKey[] = [
  'model',
  'subagent_model',
  'post_model',
  'fallback_models',
  'base_url',
  'timeout',
  'max_retries',
  'timezone',
  'memory',
  'experience_reflection',
]

/** 开关类配置：页面上用 'true' / 'false' 表示，'' 表示沿用默认值 */
const SWITCH_KEYS: ReadonlySet<SettingKey> = new Set(['experience_reflection'])

function isOn(value: SettingValue): boolean {
  if (typeof value === 'boolean') return value
  return ['on', 'true', '1', 'yes'].includes(
    String(value ?? '')
      .trim()
      .toLowerCase(),
  )
}

function splitModels(value: string) {
  return value.split(/[\s,]+/).filter(Boolean)
}

const MEMORY_OPTIONS = [
  { value: 'suggest', label: '主动建议', hint: '从对话里提议值得记住的内容，由你确认' },
  { value: 'explicit', label: '只记明确要求的', hint: '只有你说“记住…”时才保存' },
  { value: 'off', label: '关闭', hint: '不读取也不保存长期记忆' },
]

type Draft = Record<SettingKey, string>

function text(value: SettingValue): string {
  return value === null || value === undefined ? '' : String(value)
}

function initialDraft(settings: Settings): Draft {
  return Object.fromEntries(
    KEYS.map((key) => {
      const field = settings.fields[key]
      const value = field.locked ? field.effective : field.value
      if (SWITCH_KEYS.has(key)) return [key, value === null || value === undefined || value === '' ? '' : String(isOn(value))]
      return [key, text(value)]
    }),
  ) as Draft
}

export function SettingsPage() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings, staleTime: 0 })
  // 放在外层：保存后表单会按新数据重建，提示不能跟着丢
  const [savedAt, setSavedAt] = useState<number | null>(null)

  return (
    <PageScaffold title="设置" width="lg">
      <div className="grid gap-8 lg:grid-cols-[160px_minmax(0,1fr)]">
        <nav aria-label="设置分区" className="hidden lg:block">
          <ul className="sticky top-0 space-y-0.5">
            {SECTIONS.map((sec) => (
              <li key={sec.id}>
                <button
                  type="button"
                  onClick={() => document.getElementById(sec.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                  className="w-full rounded-md px-2.5 py-1.5 text-left text-[13px] text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  {sec.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="min-w-0">
          {settings.isLoading && <FormSkeleton fields={8} label="加载设置" />}
          {settings.error && <ErrorBox error={settings.error} />}
          {/* 保存后用新数据重建表单，草稿自然回到「已保存」状态 */}
          {settings.data && <SettingsForm key={settings.dataUpdatedAt} settings={settings.data} savedAt={savedAt} onSaved={setSavedAt} />}
        </div>
      </div>
    </PageScaffold>
  )
}

function SettingsForm({
  settings,
  savedAt,
  onSaved,
}: {
  settings: Settings
  savedAt: number | null
  onSaved: (at: number | null) => void
}) {
  const client = useQueryClient()
  const initial = useMemo(() => initialDraft(settings), [settings])
  const [draft, setDraft] = useState<Draft>(initial)
  const [apiKey, setApiKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const setSavedAt = onSaved
  const { fields } = settings

  const set = (key: SettingKey) => (value: string) => {
    setSavedAt(null)
    setDraft((d) => ({ ...d, [key]: value }))
  }

  const changed = KEYS.filter((key) => !fields[key].locked && draft[key].trim() !== initial[key].trim())
  const keyChanged = !settings.api_key.locked && (apiKey.trim() !== '' || clearKey)
  const dirty = changed.length > 0 || keyChanged

  const models = useQuery({ queryKey: ['models'], queryFn: api.models, staleTime: 300_000, enabled: settings.can_chat })
  // 模型列表是按已保存的接口地址拉的；正在改地址或 Key 时列表可能对不上，先不拦
  const validateModel = !changed.includes('base_url') && !keyChanged
  const modelOptions = models.data?.models ?? []
  const checkModels = validateModel && !models.isFetching
  const modelUnknown = checkModels && changed.includes('model') && isUnknownModel(draft.model, modelOptions)
  const subagentUnknown = checkModels && changed.includes('subagent_model') && isUnknownModel(draft.subagent_model, modelOptions)
  const postUnknown = checkModels && changed.includes('post_model') && isUnknownModel(draft.post_model, modelOptions)
  const unknownFallbacks =
    checkModels && changed.includes('fallback_models')
      ? splitModels(draft.fallback_models).filter((m) => isUnknownModel(m, modelOptions))
      : []
  const invalidModel = modelUnknown || subagentUnknown || postUnknown || unknownFallbacks.length > 0
  const invalidModelLabel = modelUnknown ? '默认模型' : subagentUnknown ? '子代理模型' : postUnknown ? '收尾模型' : '备用模型'
  const mainModel = draft.model.trim() || text(fields.model.effective)
  // 收尾模型留空时沿用子代理模型，子代理也没配才用主模型（与 agent._post_models 一致）
  const postDefault = draft.subagent_model.trim() || mainModel

  const save = useMutation({
    mutationFn: () => {
      const body: SettingsUpdate = { values: {} }
      for (const key of changed) {
        const raw = draft[key].trim()
        body.values[key] =
          raw === '' ? null : SWITCH_KEYS.has(key) ? raw === 'true' : key === 'timeout' || key === 'max_retries' ? Number(raw) : raw
      }
      if (clearKey) body.clear_api_key = true
      else if (apiKey.trim()) body.api_key = apiKey.trim()
      return api.saveSettings(body)
    },
    onSuccess: (next) => {
      setSavedAt(Date.now())
      client.setQueryData(['settings'], next)
      void client.invalidateQueries({ queryKey: ['meta'] })
      void client.invalidateQueries({ queryKey: ['models'] })
    },
  })

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (dirty && !invalidModel && !save.isPending) save.mutate()
  }

  function reset() {
    setDraft(initial)
    setApiKey('')
    setClearKey(false)
    save.reset()
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      {!settings.can_chat && !settings.read_only && !keyChanged && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
        >
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>还没有可用的 API Key，暂时不能新建分析。填写下面的 Key 并保存后即可使用。</span>
        </div>
      )}
      {settings.read_only && (
        <div className="rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">
          当前 serve 以 --read-only 启动：可以修改并保存配置，但本次运行不会提供提问功能。
        </div>
      )}

      <Section id="connection" title="模型连接" description="log-agent 通过 OpenAI 兼容接口调用模型">
        <ApiKeyField
          settings={settings}
          value={apiKey}
          onChange={(v) => {
            setSavedAt(null)
            setApiKey(v)
            setClearKey(false)
          }}
          clear={clearKey}
          onClear={() => {
            setSavedAt(null)
            setApiKey('')
            setClearKey((v) => !v)
          }}
        />
        <Row
          label="接口地址"
          field={fields.base_url}
          hint="OpenAI 兼容网关，如 https://llm-gateway.example.com/v1。留空使用 OpenAI 官方接口。"
        >
          {(id) => (
            <input
              id={id}
              value={draft.base_url}
              onChange={(e) => set('base_url')(e.target.value)}
              disabled={fields.base_url.locked}
              placeholder="留空使用 OpenAI 官方接口"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              className={`${FIELD} font-mono sm:max-w-md`}
            />
          )}
        </Row>
        <Row label="默认模型" field={fields.model} hint="新建分析时默认使用；也可以在新建页单独选择。">
          {(id) =>
            fields.model.locked ? (
              <input id={id} value={draft.model} disabled className={`${FIELD} font-mono sm:max-w-md`} />
            ) : (
              <ModelCombobox
                id={id}
                value={draft.model}
                onChange={set('model')}
                options={models.data?.models ?? []}
                defaultModel={text(fields.model.default)}
                loading={models.isFetching}
                validate={validateModel && changed.includes('model')}
                wrapperClassName="sm:max-w-md"
                className={`${FIELD} font-mono`}
              />
            )
          }
        </Row>
        <ConnectionTest draft={draft} apiKey={clearKey ? '' : apiKey} fields={fields} />
      </Section>

      <Section id="tiering" title="模型分层" description="把子任务交给更快、更便宜的模型">
        <Row
          label="子代理模型"
          field={fields.subagent_model}
          hint="子代理只做小范围取证（翻日志片段、查源码），可以配更便宜、更快的模型。留空与主模型相同。"
        >
          {(id) =>
            fields.subagent_model.locked ? (
              <input id={id} value={draft.subagent_model} disabled className={`${FIELD} font-mono sm:max-w-md`} />
            ) : (
              <ModelCombobox
                id={id}
                value={draft.subagent_model}
                onChange={set('subagent_model')}
                options={modelOptions}
                defaultModel={mainModel}
                loading={models.isFetching}
                validate={validateModel && changed.includes('subagent_model')}
                wrapperClassName="sm:max-w-md"
                className={`${FIELD} font-mono`}
              />
            )
          }
        </Row>
        <Row
          label="收尾模型"
          field={fields.post_model}
          hint="每轮结束后的后处理调用：把报告整理成结构化结果、按证据回查修正结论，以及排查经验的挑选、复盘与合并。只做整理不做取证，适合配便宜、快、结构化输出稳定的模型。留空沿用子代理模型（未配置则用主模型）；它失败时依次退回主模型和备用模型。"
        >
          {(id) =>
            fields.post_model.locked ? (
              <input id={id} value={draft.post_model} disabled className={`${FIELD} font-mono sm:max-w-md`} />
            ) : (
              <ModelCombobox
                id={id}
                value={draft.post_model}
                onChange={set('post_model')}
                options={modelOptions}
                defaultModel={postDefault}
                loading={models.isFetching}
                validate={validateModel && changed.includes('post_model')}
                wrapperClassName="sm:max-w-md"
                className={`${FIELD} font-mono`}
              />
            )
          }
        </Row>
        <Row
          label="备用模型"
          field={fields.fallback_models}
          hint="主模型重试后仍失败（限流、5xx、超时）时按顺序切换，主代理和子代理都生效。多个用逗号分隔，最多 5 个。"
        >
          {(id) => (
            <>
              <input
                id={id}
                value={draft.fallback_models}
                onChange={(e) => set('fallback_models')(e.target.value)}
                disabled={fields.fallback_models.locked}
                placeholder="不配置"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={unknownFallbacks.length > 0 || undefined}
                className={`${FIELD} font-mono ${unknownFallbacks.length ? 'border-red-400! dark:border-red-700!' : ''}`}
              />
              {unknownFallbacks.length > 0 && (
                <p className="text-xs text-red-600 dark:text-red-400">
                  模型列表里没有：<span className="font-mono">{unknownFallbacks.join(', ')}</span>
                </p>
              )}
            </>
          )}
        </Row>
      </Section>

      <Section id="request" title="请求">
        <Row label="超时（秒）" field={fields.timeout} hint="单次模型请求的超时，范围 1–3600。">
          {(id) => (
            <input
              id={id}
              type="number"
              min={1}
              max={3600}
              value={draft.timeout}
              onChange={(e) => set('timeout')(e.target.value)}
              disabled={fields.timeout.locked}
              placeholder={`默认 ${text(fields.timeout.default)}`}
              className={`${FIELD} w-32`}
            />
          )}
        </Row>
        <Row label="重试次数" field={fields.max_retries} hint="请求失败时自动重试，范围 0–10。">
          {(id) => (
            <input
              id={id}
              type="number"
              min={0}
              max={10}
              step={1}
              value={draft.max_retries}
              onChange={(e) => set('max_retries')(e.target.value)}
              disabled={fields.max_retries.locked}
              placeholder={`默认 ${text(fields.max_retries.default)}`}
              className={`${FIELD} w-32`}
            />
          )}
        </Row>
      </Section>

      <Section id="defaults" title="分析默认值">
        <Row label="默认时区" field={fields.timezone} hint="日志时间不带时区时按这个时区解释，如 Asia/Shanghai 或 +08:00。">
          {(id) => (
            <input
              id={id}
              value={draft.timezone}
              onChange={(e) => set('timezone')(e.target.value)}
              disabled={fields.timezone.locked}
              placeholder="默认 UTC"
              autoComplete="off"
              spellCheck={false}
              className={`${FIELD} sm:max-w-xs`}
            />
          )}
        </Row>
        <Row label="长期记忆" field={fields.memory} hint="影响之后新开始的提问。" group>
          {(id) => {
            const current = draft.memory || 'suggest'
            return (
              <div role="radiogroup" aria-labelledby={id} className="grid gap-2">
                {MEMORY_OPTIONS.map((m) => (
                  <label
                    key={m.value}
                    className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors focus-within:ring-2 focus-within:ring-ring/40 ${
                      fields.memory.locked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
                    } ${current === m.value ? 'border-primary bg-brand-soft/50' : 'border-border hover:bg-muted/50'}`}
                  >
                    <input
                      type="radio"
                      name="memory-mode"
                      value={m.value}
                      checked={current === m.value}
                      disabled={fields.memory.locked}
                      onChange={() => set('memory')(m.value)}
                      className="mt-1 accent-[var(--primary)]"
                    />
                    <span>
                      <span className="block text-[13px] font-medium text-foreground">{m.label}</span>
                      <span className="block text-xs text-muted-foreground">{m.hint}</span>
                    </span>
                  </label>
                ))}
              </div>
            )
          }}
        </Row>
      </Section>

      <Section id="experience" title="排查经验">
        <Row
          label="在线经验复盘"
          field={fields.experience_reflection}
          hint="每轮结束后多一次收尾模型（见「模型分层」）调用，复盘本轮做法：给用到的经验投票，证据回查通过时提炼候选经验（需在「经验」页确认后才生效）。影响之后新开始的提问。"
        >
          {(id) => {
            const field = fields.experience_reflection
            const checked =
              draft.experience_reflection === '' ? isOn(field.effective ?? field.default) : draft.experience_reflection === 'true'
            return (
              <Switch
                id={id}
                checked={checked}
                disabled={field.locked}
                onChange={(next) =>
                  set('experience_reflection')(next ? 'true' : initial.experience_reflection === '' && !isOn(field.default) ? '' : 'false')
                }
                label={checked ? '已开启' : '已关闭'}
              />
            )
          }}
        </Row>
      </Section>

      <p className="text-xs leading-relaxed text-muted-foreground">
        配置写入 <code className="font-mono">{settings.config_path}</code>，API Key 单独保存在{' '}
        <code className="font-mono">{settings.credentials_path}</code>（仅本人可读写）。
        {settings.project_config && (
          <>
            {' '}
            当前目录的项目配置 <code className="font-mono">{settings.project_config}</code> 优先级更高。
          </>
        )}
      </p>

      <div className="sticky bottom-0 z-10 -mx-1 flex items-center gap-3 rounded-xl border border-border bg-card/95 px-4 py-3 shadow-lg shadow-foreground/5 backdrop-blur dark:shadow-black/30">
        <div className="min-w-0 flex-1 text-[13px]" aria-live="polite">
          {save.error ? (
            <span className="inline-flex items-center gap-1.5 text-red-700 dark:text-red-300">
              <CircleAlert className="size-4 shrink-0" aria-hidden />
              {save.error.message}
            </span>
          ) : invalidModel ? (
            <span className="inline-flex items-center gap-1.5 text-red-700 dark:text-red-300">
              <CircleAlert className="size-4 shrink-0" aria-hidden />
              {invalidModelLabel}不在可用列表里，请重新选择
            </span>
          ) : dirty ? (
            <span className="inline-flex items-center gap-1.5 text-amber-700 dark:text-amber-300">
              <span className="size-2 rounded-full bg-amber-500" aria-hidden />
              有未保存的修改
            </span>
          ) : savedAt ? (
            <span className="inline-flex items-center gap-1.5 text-emerald-700 dark:text-emerald-300">
              <Check className="size-4" aria-hidden />
              已保存，立即生效
            </span>
          ) : (
            <span className="text-muted-foreground">没有未保存的修改</span>
          )}
        </div>
        <Button variant="ghost" onClick={reset} disabled={!dirty || save.isPending}>
          撤销修改
        </Button>
        <Button type="submit" variant="primary" disabled={!dirty || invalidModel || save.isPending}>
          {save.isPending && <Spinner className="border-white/40 border-t-white" />}
          保存
        </Button>
      </div>
    </form>
  )
}

const SOURCE_LABEL: Record<string, string> = {
  env: '环境变量',
  cli: '启动参数',
  project: '项目配置',
  user_section: '[chat] 配置段',
}

function SourceNote({ field }: { field: SettingField }) {
  if (field.source === 'env' || field.source === 'cli') {
    return (
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        <Lock className="h-3 w-3" aria-hidden />由{SOURCE_LABEL[field.source]} <code className="font-mono">{field.origin}</code>{' '}
        指定，网页里不能修改。
      </p>
    )
  }
  if (field.source === 'project' || field.source === 'user_section') {
    return (
      <p className="text-xs text-amber-700 dark:text-amber-400">
        当前生效的是 <code className="break-all font-mono">{field.origin}</code> 里的值
        {field.effective !== null && (
          <>
            {' '}
            <code className="font-mono">{text(field.effective)}</code>
          </>
        )}
        ，这里保存的值会被它覆盖。
      </p>
    )
  }
  return null
}

function Switch({
  id,
  checked,
  disabled,
  onChange,
  label,
}: {
  id: string
  checked: boolean
  disabled?: boolean
  onChange: (next: boolean) => void
  label: string
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50 ${
          checked ? 'bg-primary' : 'bg-input'
        }`}
      >
        <span
          aria-hidden
          className={`inline-block size-4 rounded-full bg-white shadow-sm transition-transform ${checked ? 'translate-x-4.5' : 'translate-x-0.5'}`}
        />
      </button>
      <span className="text-[13px] text-foreground" aria-hidden>
        {label}
      </span>
    </div>
  )
}

function Section({ id, title, description, children }: { id: string; title: string; description?: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-6 rounded-xl border border-border bg-card">
      <div className="border-b border-border px-5 py-3.5">
        <h2 id={`${id}-title`} className="text-sm font-semibold text-foreground">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="divide-y divide-border px-5 py-4">{children}</div>
    </section>
  )
}

const ROW = 'grid gap-2 py-4 first:pt-0 last:pb-0 sm:grid-cols-[200px_minmax(0,1fr)] sm:gap-6'

/** 左侧标签、右侧控件 + 说明；group 为 true 时标签只做分组名（单选组等没有单一可聚焦控件） */
function Row({
  label,
  field,
  hint,
  group = false,
  children,
}: {
  label: string
  field: SettingField
  hint?: string
  group?: boolean
  children: (id: string) => ReactNode
}) {
  const id = useId()
  const overridden = field.source !== 'default' && field.source !== 'user'
  return (
    <div className={ROW}>
      <div className="flex flex-wrap items-start gap-1.5">
        {group ? (
          <span id={id} className="text-[13px] font-medium text-foreground">
            {label}
          </span>
        ) : (
          <label htmlFor={id} className="text-[13px] font-medium text-foreground">
            {label}
          </label>
        )}
        {overridden && <Badge tone={field.locked ? 'gray' : 'amber'}>{SOURCE_LABEL[field.source]}</Badge>}
      </div>
      <div className="min-w-0 space-y-1.5">
        {children(id)}
        {overridden ? <SourceNote field={field} /> : hint && <p className="text-xs leading-5 text-muted-foreground">{hint}</p>}
      </div>
    </div>
  )
}

function ApiKeyField({
  settings,
  value,
  onChange,
  clear,
  onClear,
}: {
  settings: Settings
  value: string
  onChange: (value: string) => void
  clear: boolean
  onClear: () => void
}) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  const key = settings.api_key

  return (
    <div className={ROW}>
      <div className="flex flex-wrap items-start gap-1.5">
        <label htmlFor={id} className="text-[13px] font-medium text-foreground">
          API Key
        </label>
        {key.set ? (
          <Badge tone="green">
            已设置 <span className="font-mono">{key.masked}</span>
          </Badge>
        ) : (
          <Badge tone="amber">未设置</Badge>
        )}
        {key.source === 'env' && <Badge tone="gray">环境变量</Badge>}
      </div>
      <div className="min-w-0 space-y-1.5">
        {key.locked ? (
          <p className="flex items-center gap-1 pt-1 text-xs text-muted-foreground">
            <Lock className="size-3" aria-hidden />
            由环境变量 <code className="font-mono">OPENAI_API_KEY</code> 提供，网页里不能修改。
          </p>
        ) : (
          <>
            <div className="flex gap-2 sm:max-w-md">
              <div className="relative flex-1">
                <input
                  id={id}
                  type={visible ? 'text' : 'password'}
                  value={value}
                  onChange={(e) => onChange(e.target.value)}
                  placeholder={key.saved ? '粘贴新的 Key 替换（留空保持不变）' : 'sk-...'}
                  autoComplete="new-password"
                  spellCheck={false}
                  disabled={clear}
                  className={`${FIELD} pr-10 font-mono`}
                />
                <button
                  type="button"
                  onClick={() => setVisible((v) => !v)}
                  aria-label={visible ? '隐藏 Key' : '显示 Key'}
                  className="absolute inset-y-0 right-0 flex w-9 items-center justify-center rounded-r-lg text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
              {key.saved && (
                <Button variant={clear ? 'secondary' : 'danger'} className="h-9" onClick={onClear}>
                  {clear ? '不清除' : '清除'}
                </Button>
              )}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              {clear ? (
                '保存后会删除已保存的 Key。'
              ) : (
                <>
                  Key 只保存在本机，页面不会回显完整内容。环境变量 <code className="font-mono">OPENAI_API_KEY</code>{' '}
                  存在时优先使用环境变量。
                </>
              )}
            </p>
          </>
        )}
      </div>
    </div>
  )
}

function ConnectionTest({ draft, apiKey, fields }: { draft: Draft; apiKey: string; fields: Settings['fields'] }) {
  const test = useMutation<ConnectionTestResult>({
    mutationFn: () =>
      api.testConnection({
        model: draft.model.trim() || undefined,
        base_url: fields.base_url.locked ? undefined : draft.base_url.trim(),
        api_key: apiKey.trim() || undefined,
      }),
  })
  const result = test.data

  return (
    <div className="flex flex-wrap items-center gap-3 py-4 last:pb-0 sm:pl-[224px]">
      <Button onClick={() => test.mutate()} disabled={test.isPending} className="shrink-0">
        {test.isPending ? <Spinner /> : <PlugZap className="size-3.5" aria-hidden />}
        测试连接
      </Button>
      <div className="min-w-0 text-xs" aria-live="polite">
        {test.isPending ? (
          <span className="text-muted-foreground">正在发送一次最小请求…</span>
        ) : test.error ? (
          <span className="inline-flex items-center gap-1 text-red-700 dark:text-red-300">
            <CircleAlert className="size-3.5 shrink-0" aria-hidden />
            {test.error.message}
          </span>
        ) : result ? (
          <span className={result.ok ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-700 dark:text-red-300'}>
            <span className="inline-flex items-center gap-1">
              {result.ok ? <Check className="size-3.5 shrink-0" aria-hidden /> : <CircleAlert className="size-3.5 shrink-0" aria-hidden />}
              {result.ok ? `连接成功 · ${result.latency}s · ${result.message}` : result.message}
            </span>
            <span className="block text-muted-foreground">
              {result.endpoint} · <span className="font-mono">{result.served_model || result.model}</span>
            </span>
          </span>
        ) : (
          <span className="text-muted-foreground">无需先保存，使用当前填写的值测试</span>
        )}
      </div>
    </div>
  )
}

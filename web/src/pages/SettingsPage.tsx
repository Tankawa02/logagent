import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CircleAlert, Eye, EyeOff, KeyRound, Lock, PlugZap, RotateCcw, Save } from 'lucide-react'
import { useId, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { isUnknownModel, ModelCombobox } from '../components/ModelCombobox'
import { Select } from '../components/Select'
import { Badge, Button, Card, CardHeader, ErrorBox, FormSkeleton, Spinner } from '../components/ui'
import { api } from '../lib/api'
import type { ConnectionTestResult, SettingField, SettingKey, Settings, SettingsUpdate, SettingValue } from '../lib/types'

const FIELD =
  'w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm outline-none transition-colors placeholder:text-zinc-400 focus:border-brand-400 focus:ring-4 focus:ring-brand-500/10 disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-500 dark:border-zinc-700 dark:bg-zinc-950 dark:disabled:bg-zinc-900'

const KEYS: SettingKey[] = ['model', 'base_url', 'timeout', 'max_retries', 'timezone', 'memory']

const MEMORY_OPTIONS = [
  { value: 'suggest', label: '主动建议：从对话里提议值得记住的内容，由你确认' },
  { value: 'explicit', label: '只记我明确要求记住的内容' },
  { value: 'off', label: '关闭长期记忆' },
]

type Draft = Record<SettingKey, string>

function text(value: SettingValue): string {
  return value === null || value === undefined ? '' : String(value)
}

function initialDraft(settings: Settings): Draft {
  return Object.fromEntries(
    KEYS.map((key) => {
      const field = settings.fields[key]
      return [key, text(field.locked ? field.effective : field.value)]
    }),
  ) as Draft
}

export function SettingsPage() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings, staleTime: 0 })
  // 放在外层：保存后表单会按新数据重建，提示不能跟着丢
  const [savedAt, setSavedAt] = useState<number | null>(null)

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:py-10">
        <header className="space-y-1.5">
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">设置</h1>
          <p className="text-pretty text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
            模型连接与常用默认值。保存后立即对当前 serve 生效，不用重启；命令行的 analyze / chat 也读同一份配置。
          </p>
        </header>
        {settings.isLoading && <FormSkeleton fields={6} label="加载设置" />}
        {settings.error && <ErrorBox error={settings.error} />}
        {/* 保存后用新数据重建表单，草稿自然回到「已保存」状态 */}
        {settings.data && <SettingsForm key={settings.dataUpdatedAt} settings={settings.data} savedAt={savedAt} onSaved={setSavedAt} />}
      </div>
    </div>
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
  const modelUnknown =
    validateModel && changed.includes('model') && !models.isFetching && isUnknownModel(draft.model, models.data?.models ?? [])

  const save = useMutation({
    mutationFn: () => {
      const body: SettingsUpdate = { values: {} }
      for (const key of changed) {
        const raw = draft[key].trim()
        body.values[key] = raw === '' ? null : key === 'timeout' || key === 'max_retries' ? Number(raw) : raw
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
    if (dirty && !modelUnknown && !save.isPending) save.mutate()
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
        <div className="rounded-xl border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
          当前 serve 以 --read-only 启动：可以修改并保存配置，但本次运行不会提供提问功能。
        </div>
      )}

      <Card>
        <CardHeader title="模型连接" />
        <div className="space-y-5 px-4 py-5">
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
                className={`${FIELD} font-mono`}
              />
            )}
          </Row>
          <Row label="默认模型" field={fields.model} hint="新建分析时默认使用；也可以在新建页单独选择。">
            {(id) =>
              fields.model.locked ? (
                <input id={id} value={draft.model} disabled className={`${FIELD} font-mono`} />
              ) : (
                <ModelCombobox
                  id={id}
                  value={draft.model}
                  onChange={set('model')}
                  options={models.data?.models ?? []}
                  defaultModel={text(fields.model.default)}
                  loading={models.isFetching}
                  validate={validateModel && changed.includes('model')}
                  className={`${FIELD} font-mono`}
                />
              )
            }
          </Row>
          <ConnectionTest draft={draft} apiKey={clearKey ? '' : apiKey} fields={fields} />
        </div>
      </Card>

      <Card>
        <CardHeader title="请求" />
        <div className="grid gap-5 px-4 py-5 sm:grid-cols-2">
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
                className={FIELD}
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
                className={FIELD}
              />
            )}
          </Row>
        </div>
      </Card>

      <Card>
        <CardHeader title="分析默认值" />
        <div className="space-y-5 px-4 py-5">
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
                className={FIELD}
              />
            )}
          </Row>
          <Row label="长期记忆" field={fields.memory} hint="影响之后新开始的提问。">
            {(id) => (
              <Select
                id={id}
                value={draft.memory}
                onChange={set('memory')}
                options={[{ value: '', label: '默认（主动建议）' }, ...MEMORY_OPTIONS]}
                size="lg"
              />
            )}
          </Row>
        </div>
      </Card>

      <p className="text-xs leading-relaxed text-zinc-400">
        配置写入 <code className="font-mono">{settings.config_path}</code>，API Key 单独保存在{' '}
        <code className="font-mono">{settings.credentials_path}</code>（仅本人可读写）。
        {settings.project_config && (
          <>
            {' '}
            当前目录的项目配置 <code className="font-mono">{settings.project_config}</code> 优先级更高。
          </>
        )}
      </p>

      <div className="sticky bottom-0 -mx-4 flex flex-wrap items-center justify-end gap-3 border-t border-zinc-200/80 bg-white/90 px-4 py-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/90">
        <div className="mr-auto min-w-0 text-sm" aria-live="polite">
          {save.error ? (
            <span className="text-red-600 dark:text-red-400">{save.error.message}</span>
          ) : savedAt && !dirty ? (
            <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
              <Check className="h-4 w-4" aria-hidden />
              已保存，立即生效
            </span>
          ) : modelUnknown ? (
            <span className="text-red-600 dark:text-red-400">默认模型不在可用列表里，请重新选择</span>
          ) : dirty ? (
            <span className="text-zinc-500">有未保存的修改</span>
          ) : null}
        </div>
        <Button variant="ghost" onClick={reset} disabled={!dirty || save.isPending}>
          <RotateCcw className="h-4 w-4" aria-hidden />
          撤销修改
        </Button>
        <Button type="submit" variant="primary" disabled={!dirty || modelUnknown || save.isPending}>
          {save.isPending ? <Spinner className="border-white/40 border-t-white" /> : <Save className="h-4 w-4" aria-hidden />}
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
      <p className="flex items-center gap-1 text-xs text-zinc-500">
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

function Row({ label, field, hint, children }: { label: string; field: SettingField; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId()
  const overridden = field.source !== 'default' && field.source !== 'user'
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <label htmlFor={id} className="text-xs font-medium text-zinc-700 dark:text-zinc-200">
          {label}
        </label>
        {overridden && <Badge tone={field.locked ? 'gray' : 'amber'}>{SOURCE_LABEL[field.source]}</Badge>}
      </div>
      {children(id)}
      {overridden ? <SourceNote field={field} /> : hint && <p className="text-xs text-zinc-400">{hint}</p>}
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
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="flex items-center gap-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200">
          <KeyRound className="h-3.5 w-3.5 text-zinc-400" aria-hidden />
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
      {key.locked ? (
        <p className="flex items-center gap-1 text-xs text-zinc-500">
          <Lock className="h-3 w-3" aria-hidden />
          由环境变量 <code className="font-mono">OPENAI_API_KEY</code> 提供，网页里不能修改。
        </p>
      ) : (
        <>
          <div className="flex gap-2">
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
                className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
              >
                {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            {key.saved && (
              <Button variant={clear ? 'secondary' : 'danger'} onClick={onClear}>
                {clear ? '不清除' : '清除'}
              </Button>
            )}
          </div>
          <p className="text-xs text-zinc-400">
            {clear
              ? '保存后会删除已保存的 Key。'
              : 'Key 只保存在本机，页面不会回显完整内容。也可以继续用环境变量 OPENAI_API_KEY（优先级更高）。'}
          </p>
        </>
      )}
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
    <div className="flex flex-col gap-3 rounded-lg border border-dashed border-zinc-200 bg-zinc-50/60 px-3 py-3 sm:flex-row sm:items-center dark:border-zinc-800 dark:bg-zinc-950/40">
      <Button onClick={() => test.mutate()} disabled={test.isPending} className="shrink-0">
        {test.isPending ? <Spinner /> : <PlugZap className="h-4 w-4" aria-hidden />}
        测试连接
      </Button>
      <div className="min-w-0 text-sm" aria-live="polite">
        {test.isPending ? (
          <span className="text-zinc-500">正在发送一次最小请求…</span>
        ) : test.error ? (
          <span className="text-red-600 dark:text-red-400">{test.error.message}</span>
        ) : result ? (
          <span className={result.ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
            {result.ok ? `连接成功 · ${result.latency}s · ${result.message}` : result.message}
            <span className="block text-xs text-zinc-400">
              {result.endpoint} · <span className="font-mono">{result.served_model || result.model}</span>
            </span>
          </span>
        ) : (
          <span className="text-zinc-500">用上面填写的地址、模型和 Key 发一次请求，不需要先保存。</span>
        )}
      </div>
    </div>
  )
}

import { Check, ChevronDown } from 'lucide-react'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './Menu'

export const MENU_TRIGGER =
  'inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-[13px] text-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/40'

export function ScopeMenu({
  value,
  options,
  onChange,
  label,
  prefix,
  align = 'start',
}: {
  value: string
  options: { value: string; label: string; title?: string }[]
  onChange: (value: string) => void
  label: string
  prefix?: string
  align?: 'start' | 'end'
}) {
  const current = options.find((o) => o.value === value)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={MENU_TRIGGER} aria-label={label}>
        {prefix && <span className="text-muted-foreground">{prefix}</span>}
        <span className="max-w-40 truncate">{current?.label ?? value}</span>
        <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} className="w-52">
        {options.map((o) => (
          <DropdownMenuItem key={o.value} title={o.title} onSelect={() => onChange(o.value)}>
            <Check className={o.value === value ? 'opacity-100' : 'opacity-0'} aria-hidden />
            <span className="truncate">{o.label}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

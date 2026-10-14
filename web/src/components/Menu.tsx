import * as Radix from '@radix-ui/react-dropdown-menu'
import { forwardRef, type ComponentPropsWithoutRef } from 'react'

/**
 * 下拉菜单渲染在 portal 里：侧边栏、卡片这些滚动容器不会把它裁掉，靠近屏幕边缘时自动翻转。
 */
export const DropdownMenu = Radix.Root
export const DropdownMenuTrigger = Radix.Trigger

export const DropdownMenuContent = forwardRef<HTMLDivElement, ComponentPropsWithoutRef<typeof Radix.Content>>(function DropdownMenuContent(
  { className = '', sideOffset = 6, collisionPadding = 8, align = 'start', ...props },
  ref,
) {
  return (
    <Radix.Portal>
      <Radix.Content
        ref={ref}
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        align={align}
        className={`z-[60] max-h-[var(--radix-dropdown-menu-content-available-height)] min-w-[8rem] origin-[var(--radix-dropdown-menu-content-transform-origin)] overflow-y-auto overflow-x-hidden rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg data-[state=open]:animate-menu-in ${className}`}
        {...props}
      />
    </Radix.Portal>
  )
})

interface ItemProps extends ComponentPropsWithoutRef<typeof Radix.Item> {
  variant?: 'default' | 'destructive'
}

export const DropdownMenuItem = forwardRef<HTMLDivElement, ItemProps>(function DropdownMenuItem(
  { className = '', variant = 'default', ...props },
  ref,
) {
  const tone =
    variant === 'destructive'
      ? 'text-red-600 focus:bg-red-50 focus:text-red-700 dark:text-red-400 dark:focus:bg-red-500/10 [&_svg]:!text-current'
      : 'focus:bg-accent focus:text-accent-foreground'
  return (
    <Radix.Item
      ref={ref}
      className={`relative flex cursor-default select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-muted-foreground ${tone} ${className}`}
      {...props}
    />
  )
})

export function DropdownMenuSeparator({ className = '', ...props }: ComponentPropsWithoutRef<typeof Radix.Separator>) {
  return <Radix.Separator className={`-mx-1 my-1 h-px bg-border ${className}`} {...props} />
}

export function DropdownMenuLabel({ className = '', ...props }: ComponentPropsWithoutRef<typeof Radix.Label>) {
  return <Radix.Label className={`px-2 py-1.5 text-xs font-medium text-muted-foreground ${className}`} {...props} />
}

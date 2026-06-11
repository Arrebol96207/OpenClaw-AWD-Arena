import React, { useState } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'

export const cx = (...classes: Array<string | false | null | undefined>): string =>
  classes.filter(Boolean).join(' ')

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'outline'

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'bg-white text-neutral-900 hover:bg-neutral-100 font-medium',
  secondary: 'bg-neutral-800/80 text-neutral-200 hover:bg-neutral-700/80 font-medium',
  outline: 'border border-neutral-700 text-neutral-200 hover:bg-neutral-800 font-medium',
  ghost: 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/60',
}

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  icon?: React.ReactNode
  loading?: boolean
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'secondary',
  size = 'md',
  icon,
  loading = false,
  className,
  children,
  disabled,
  type,
  ...props
}) => (
  <button
    type={type ?? 'button'}
    disabled={disabled || loading}
    {...props}
    className={cx(
      'inline-flex items-center justify-center gap-1.5 rounded-lg transition-colors',
      'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-neutral-400',
      'disabled:pointer-events-none disabled:opacity-50',
      size === 'sm' ? 'h-8 px-3 text-xs' : 'h-9 px-4 text-sm',
      buttonVariants[variant],
      className,
    )}
  >
    {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
    {children}
  </button>
)

export const Card: React.FC<{
  title?: string
  description?: string
  className?: string
  children: React.ReactNode
  action?: React.ReactNode
}> = ({ title, description, className, children, action }) => (
  <div className={cx('rounded-xl bg-neutral-900/80 ring-1 ring-neutral-800/60', className)}>
    {(title || action) && (
      <div className="flex items-center justify-between border-b border-neutral-800/60 px-5 py-4">
        <div>
          {title && <h3 className="text-sm font-semibold text-neutral-100">{title}</h3>}
          {description && <p className="mt-0.5 text-xs text-neutral-500">{description}</p>}
        </div>
        {action}
      </div>
    )}
    <div className="p-5">{children}</div>
  </div>
)

export const Panel = Card

export const CollapsiblePanel: React.FC<{
  title: string
  description?: string
  defaultOpen?: boolean
  className?: string
  children: React.ReactNode
}> = ({ title, description, defaultOpen = false, className, children }) => {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className={cx('rounded-xl bg-neutral-900/80 ring-1 ring-neutral-800/60', className)}>
      <button
        type="button"
        className="flex w-full items-center justify-between px-5 py-4 text-left"
        onClick={() => setOpen(!open)}
      >
        <div>
          <h3 className="text-sm font-semibold text-neutral-100">{title}</h3>
          {description && <p className="mt-0.5 text-xs text-neutral-500">{description}</p>}
        </div>
        <ChevronDown className={cx('h-4 w-4 text-neutral-500 transition-transform', open && 'rotate-180')} />
      </button>
      {open && <div className="border-t border-neutral-800/60 px-5 py-4">{children}</div>}
    </div>
  )
}

type BadgeVariant = 'default' | 'success' | 'warning' | 'error'

const badgeVariants: Record<BadgeVariant, string> = {
  default: 'bg-neutral-800 text-neutral-300',
  success: 'bg-emerald-500/10 text-emerald-400',
  warning: 'bg-amber-500/10 text-amber-400',
  error: 'bg-red-500/10 text-red-400',
}

export const Badge: React.FC<{
  variant?: BadgeVariant
  className?: string
  children: React.ReactNode
}> = ({ variant = 'default', className, children }) => (
  <span className={cx('inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium', badgeVariants[variant], className)}>
    {children}
  </span>
)

export const StatusBadge: React.FC<{
  tone?: 'neutral' | 'info' | 'success' | 'warning' | 'danger'
  className?: string
  children: React.ReactNode
}> = ({ tone = 'neutral', className, children }) => {
  const variantMap: Record<string, BadgeVariant> = {
    neutral: 'default', info: 'default', success: 'success', warning: 'warning', danger: 'error',
  }
  return <Badge variant={variantMap[tone]} className={className}>{children}</Badge>
}

export const Field: React.FC<{
  label: string
  hint?: string
  className?: string
  children: React.ReactNode
}> = ({ label, hint, className, children }) => (
  <div className={cx('space-y-1.5', className)}>
    <label className="block text-xs font-medium text-neutral-400">{label}</label>
    {children}
    {hint && <p className="text-xs text-neutral-600">{hint}</p>}
  </div>
)

export const inputClassName =
  'w-full rounded-lg border border-neutral-800 bg-neutral-950 px-3.5 py-2 text-sm text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-500 disabled:cursor-not-allowed disabled:opacity-50'

export const tableClassName = 'w-full text-sm'

export const ErrorBanner: React.FC<{ message: string | null; className?: string }> = ({ message, className }) => {
  if (!message) return null
  return (
    <div role="alert" className={cx('rounded-lg bg-red-500/10 px-4 py-3 text-sm text-red-400', className)}>
      {message}
    </div>
  )
}

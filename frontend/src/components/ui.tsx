import React, { useState } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'

export const cx = (...classes: Array<string | false | null | undefined>): string =>
  classes.filter(Boolean).join(' ')

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'warning'

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'bg-[#B19CD9] text-white hover:bg-[#9b84c7] shadow-sm',
  secondary: 'bg-[#2a2a3a] text-[#D3D3D3] border border-[#3a3a4a] hover:bg-[#333345]',
  ghost: 'text-[#8888aa] hover:text-[#D3D3D3] hover:bg-[#2a2a3a]',
  danger: 'bg-red-600 text-white hover:bg-red-700 shadow-sm',
  warning: 'bg-amber-600 text-white hover:bg-amber-700 shadow-sm',
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
      'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#B19CD9]/40',
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
  className?: string
  children: React.ReactNode
  action?: React.ReactNode
}> = ({ title, className, children, action }) => (
  <div className={cx('rounded-xl bg-[#1e1e2e]/80 border border-[#2a2a3a]', className)}>
    {(title || action) && (
      <div className="flex items-center justify-between border-b border-[#2a2a3a] px-5 py-3">
        {title && <h3 className="text-sm font-medium text-[#D3D3D3]">{title}</h3>}
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
    <div className={cx('rounded-xl bg-[#1e1e2e]/80 border border-[#2a2a3a]', className)}>
      <button
        type="button"
        className="flex w-full items-center justify-between px-5 py-3 text-left"
        onClick={() => setOpen(!open)}
      >
        <div>
          <h3 className="text-sm font-medium text-[#D3D3D3]">{title}</h3>
          {description && <p className="mt-0.5 text-xs text-[#6a6a8a]">{description}</p>}
        </div>
        <ChevronDown className={cx('h-4 w-4 text-[#6a6a8a] transition-transform', open && 'rotate-180')} />
      </button>
      {open && <div className="border-t border-[#2a2a3a] px-5 py-4">{children}</div>}
    </div>
  )
}

type BadgeVariant = 'default' | 'success' | 'warning' | 'error'

const badgeVariants: Record<BadgeVariant, string> = {
  default: 'bg-[#2a2a3a] text-[#D3D3D3]',
  success: 'bg-emerald-500/20 text-emerald-400',
  warning: 'bg-amber-500/20 text-amber-400',
  error: 'bg-red-500/20 text-red-400',
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
    <label className="block text-xs font-medium text-[#8888aa]">{label}</label>
    {children}
    {hint && <p className="text-xs text-[#6a6a8a]">{hint}</p>}
  </div>
)

export const inputClassName =
  'w-full rounded-lg border border-[#2a2a3a] bg-[#16162a]/80 px-3 py-2 text-sm text-[#D3D3D3] placeholder:text-[#4a4a6a] focus:border-[#B19CD9]/50 focus:outline-none focus:ring-1 focus:ring-[#B19CD9]/20 disabled:cursor-not-allowed disabled:opacity-50 transition-colors'

export const tableClassName = 'w-full text-sm'

export const ErrorBanner: React.FC<{ message: string | null; className?: string }> = ({ message, className }) => {
  if (!message) return null
  return (
    <div role="alert" className={cx('rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400', className)}>
      {message}
    </div>
  )
}

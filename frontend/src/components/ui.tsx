import React, { useState } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'

export const cx = (...classes: Array<string | false | null | undefined>): string =>
  classes.filter(Boolean).join(' ')

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'warning' | 'ghost'
type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'bg-cyan-600 text-white hover:bg-cyan-500 focus:ring-cyan-400/40',
  secondary: 'bg-slate-800 text-slate-200 hover:bg-slate-700 focus:ring-slate-400/30',
  danger: 'bg-rose-600 text-white hover:bg-rose-500 focus:ring-rose-400/40',
  warning: 'bg-amber-600 text-white hover:bg-amber-500 focus:ring-amber-400/40',
  ghost: 'bg-transparent text-slate-400 hover:bg-slate-800 hover:text-slate-200 focus:ring-slate-400/30',
}

export const badgeTones: Record<BadgeTone, string> = {
  neutral: 'bg-slate-800 text-slate-300',
  info: 'bg-cyan-900/50 text-cyan-300',
  success: 'bg-emerald-900/50 text-emerald-300',
  warning: 'bg-amber-900/50 text-amber-300',
  danger: 'bg-rose-900/50 text-rose-300',
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
    aria-busy={loading || undefined}
    {...props}
    className={cx(
      'inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-md font-medium transition',
      'focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50',
      size === 'sm' ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-sm',
      buttonVariants[variant],
      className,
    )}
  >
    {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
    {children}
  </button>
)

export const Panel: React.FC<React.HTMLAttributes<HTMLElement> & { title?: string }> = ({
  title,
  className,
  children,
  ...props
}) => (
  <section
    {...props}
    className={cx('rounded-lg border border-slate-800 bg-slate-900/50 p-4', className)}
  >
    {title && (
      <h2 className="mb-3 text-sm font-medium text-slate-300">{title}</h2>
    )}
    {children}
  </section>
)

export const CollapsiblePanel: React.FC<{
  title: string
  defaultOpen?: boolean
  className?: string
  children: React.ReactNode
}> = ({ title, defaultOpen = false, className, children }) => {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <section className={cx('rounded-lg border border-slate-800 bg-slate-900/50', className)}>
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-slate-300 hover:bg-slate-800/50"
        onClick={() => setOpen(!open)}
      >
        <span>{title}</span>
        <ChevronDown className={cx('h-4 w-4 text-slate-500 transition-transform', open && 'rotate-180')} />
      </button>
      {open && <div className="border-t border-slate-800 px-4 py-3">{children}</div>}
    </section>
  )
}

export const StatusBadge: React.FC<React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }> = ({
  tone = 'neutral',
  className,
  children,
  ...props
}) => (
  <span
    {...props}
    className={cx(
      'inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs',
      badgeTones[tone],
      className,
    )}
  >
    {children}
  </span>
)

export const Field: React.FC<React.LabelHTMLAttributes<HTMLLabelElement> & { label: string; hint?: string }> = ({
  label,
  hint,
  className,
  children,
  ...props
}) => (
  <label {...props} className={cx('block space-y-1 text-sm', className)}>
    <span className="text-slate-400">{label}</span>
    {children}
    {hint && <span className="block text-xs text-slate-600">{hint}</span>}
  </label>
)

export const inputClassName =
  'w-full rounded border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-600 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30 disabled:opacity-60'

export const tableClassName = 'w-full min-w-[760px] text-sm'

export const ErrorBanner: React.FC<{ message: string | null; className?: string }> = ({ message, className }) => {
  if (!message) return null
  return (
    <div
      role="alert"
      aria-live="polite"
      className={cx('rounded border border-rose-500/30 bg-rose-950/30 px-3 py-2 text-sm text-rose-200', className)}
    >
      {message}
    </div>
  )
}

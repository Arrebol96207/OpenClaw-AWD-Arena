import React from 'react'
import { Loader2 } from 'lucide-react'

export const cx = (...classes: Array<string | false | null | undefined>): string =>
  classes.filter(Boolean).join(' ')

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'warning' | 'ghost'
type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'border-cyan-500/60 bg-cyan-500/90 text-slate-950 hover:bg-cyan-400 focus:ring-cyan-400/40',
  secondary: 'border-slate-600 bg-slate-800 text-slate-100 hover:bg-slate-700 focus:ring-slate-400/30',
  danger: 'border-rose-500/60 bg-rose-600/90 text-white hover:bg-rose-500 focus:ring-rose-400/40',
  warning: 'border-amber-500/60 bg-amber-600/90 text-white hover:bg-amber-500 focus:ring-amber-400/40',
  ghost: 'border-transparent bg-transparent text-slate-300 hover:bg-slate-800 hover:text-white focus:ring-slate-400/30',
}

export const badgeTones: Record<BadgeTone, string> = {
  neutral: 'border-slate-600 bg-slate-800/80 text-slate-200',
  info: 'border-cyan-500/40 bg-cyan-950/50 text-cyan-200',
  success: 'border-emerald-500/40 bg-emerald-950/50 text-emerald-200',
  warning: 'border-amber-500/40 bg-amber-950/50 text-amber-200',
  danger: 'border-rose-500/40 bg-rose-950/50 text-rose-200',
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
      'inline-flex cursor-pointer items-center justify-center gap-2 rounded-md border font-medium transition-all duration-200',
      'focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50',
      size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3.5 py-2 text-sm',
      buttonVariants[variant],
      className,
    )}
  >
    {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
    {children}
  </button>
)

export const Panel: React.FC<React.HTMLAttributes<HTMLElement> & { title?: string; eyebrow?: string }> = ({
  title,
  eyebrow,
  className,
  children,
  ...props
}) => (
  <section
    {...props}
    className={cx('rounded-lg border border-slate-700/80 bg-slate-900/70 p-4 shadow-lg shadow-black/10', className)}
  >
    {(title || eyebrow) && (
      <div className="mb-4">
        {eyebrow && <div className="text-xs font-medium uppercase tracking-wider text-cyan-300/80">{eyebrow}</div>}
        {title && <h2 className="mt-1 text-lg font-semibold text-slate-100">{title}</h2>}
      </div>
    )}
    {children}
  </section>
)

export const StatusBadge: React.FC<React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }> = ({
  tone = 'neutral',
  className,
  children,
  ...props
}) => (
  <span
    {...props}
    className={cx(
      'inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs font-medium',
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
  <label {...props} className={cx('block space-y-1.5 text-sm', className)}>
    <span className="text-slate-300">{label}</span>
    {children}
    {hint && <span className="block text-xs text-slate-500">{hint}</span>}
  </label>
)

export const inputClassName =
  'w-full rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm text-slate-100 outline-none transition duration-200 placeholder:text-slate-500 focus:border-cyan-400 focus:ring-2 focus:ring-cyan-400/20 disabled:opacity-60'

export const tableClassName = 'w-full min-w-[760px] text-sm'

export const ErrorBanner: React.FC<{ message: string | null; className?: string }> = ({ message, className }) => {
  if (!message) return null
  return (
    <div
      role="alert"
      aria-live="polite"
      className={cx('rounded-md border border-rose-500/40 bg-rose-950/40 px-3 py-2 text-sm text-rose-100', className)}
    >
      {message}
    </div>
  )
}

import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

type BadgeVariant = 'neutral' | 'accent' | 'ok' | 'danger' | 'patch' | 'unaffected' | 'skipped'

const variants: Record<BadgeVariant, string> = {
  neutral: 'border-border-lit bg-surface-2 text-text-dim',
  accent: 'border-accent/25 bg-accent/8 text-accent',
  ok: 'border-ok/25 bg-ok/8 text-ok',
  danger: 'border-danger/25 bg-danger/8 text-danger',
  patch: 'border-patch/25 bg-patch/8 text-patch',
  unaffected: 'border-unaffected/25 bg-unaffected/8 text-unaffected',
  skipped: 'border-skipped/25 bg-skipped/8 text-skipped',
}

export function Badge({ className, variant = 'neutral', ...props }: HTMLAttributes<HTMLSpanElement> & { variant?: BadgeVariant }) {
  return (
    <span
      className={cn(
        'inline-flex h-6 shrink-0 items-center gap-1.5 rounded-[6px] border px-2 text-[10px] font-semibold uppercase tracking-[0.08em]',
        variants[variant],
        className,
      )}
      {...props}
    />
  )
}

import {
  Activity,
  Braces,
  Github,
  GitPullRequest,
  Network,
  RotateCcw,
  ScanSearch,
  ShieldCheck,
} from 'lucide-react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { useApp } from '../context/app-context'
import { cn, motionEase, timeAgo } from '../lib/utils'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Separator } from './ui/separator'
import { Tooltip } from './ui/tooltip'

const nav = [
  { to: '/demo', label: 'Overview', icon: Activity },
  { to: '/demo/schema', label: 'Schema change', icon: Braces },
  { to: '/demo/impact', label: 'Dependency map', icon: Network },
  { to: '/demo/patches', label: 'Proposed changes', icon: ScanSearch },
  { to: '/demo/pr', label: 'GitHub delivery', icon: GitPullRequest },
  { to: '/demo/writeback', label: 'Audit trail', icon: ShieldCheck },
]

export function AppShell() {
  const location = useLocation()
  const { health, healthError, currentRun, resetDemo, resetting } = useApp()
  const [resetError, setResetError] = useState<string | null>(null)

  const handleReset = async () => {
    setResetError(null)
    try {
      await resetDemo()
    } catch (error) {
      setResetError(error instanceof Error ? error.message : 'The demo could not be reset.')
    }
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      <aside className="fixed inset-y-0 left-0 z-40 flex w-[236px] flex-col border-r border-border bg-white px-3 pb-3 pt-4">
        <div className="px-2 pb-5">
          <Link to="/" aria-label="Relink homepage" className="inline-flex"><img src="/relink-logo.png" alt="Relink" className="h-10 w-auto max-w-[174px] object-contain object-left" /></Link>
          <div className="mt-1 text-[10px] text-text-dim">Connected-data repair agent</div>
        </div>

        <div className="px-2 pb-2 text-[9px] font-semibold uppercase tracking-[0.14em] text-text-faint">Workspace</div>
        <nav aria-label="Primary" className="space-y-1">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/demo'}
              className={({ isActive }) => cn(
                'group flex h-10 items-center gap-3 rounded-[7px] border border-transparent px-3 text-[12px] font-medium text-text-dim transition-all duration-150 hover:bg-accent/5 hover:text-accent',
                isActive && 'border-accent/10 bg-accent/8 text-accent',
              )}
            >
              <Icon className="size-4 text-text-faint transition-colors group-hover:text-accent" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto"><Separator /></div>
      </aside>

      <div className="ml-[236px] min-h-screen min-w-0">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-white/95 px-7 backdrop-blur-xl">
          <div className="flex min-w-0 items-center gap-3">
            <Github className="size-4 text-text-faint" />
            <span className="text-[11px] font-semibold text-text">Relink</span>
            <span className="text-text-faint">/</span>
            <span className="text-[11px] text-text-dim">pull request automation</span>
            {currentRun ? (
              <>
                <Tooltip content={currentRun.id}>
                  <span className="max-w-[260px] truncate font-mono text-[10px] text-text-dim">{currentRun.id}</span>
                </Tooltip>
                <Badge variant={currentRun.status === 'succeeded' ? 'ok' : currentRun.status === 'failed' ? 'danger' : 'accent'}>
                  {currentRun.status === 'running' && <span className="size-1 rounded-full bg-current [animation:status-pulse_1.2s_ease-in-out_infinite]" />}
                  {currentRun.status}
                </Badge>
                {currentRun.degraded && <Badge variant="patch">degraded</Badge>}
                <span className="hidden text-[10px] text-text-faint xl:inline">started {timeAgo(currentRun.started_at)}</span>
              </>
            ) : (
              <Badge variant="neutral">Live demo</Badge>
            )}
          </div>
          <Button variant="outline" size="sm" disabled={resetting} onClick={() => void handleReset()}>
            <RotateCcw className={cn('size-3.5', resetting && 'animate-spin')} />
            {resetting ? 'Resetting' : 'Reset demo'}
          </Button>
        </header>

        <AnimatePresence mode="wait">
          <motion.main
            key={location.pathname}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -3 }}
            transition={{ duration: 0.2, ease: motionEase }}
            className="mx-auto min-w-0 max-w-[1440px] px-7 py-6"
          >
            {(healthError || resetError) && (
              <div role="alert" className="mb-4 flex items-center gap-2 rounded-[8px] border border-danger/25 bg-danger/5 px-3 py-2 text-[11px] text-danger">
                <span className="size-1.5 rounded-full bg-danger" />
                {resetError ?? healthError}
              </div>
            )}
            {health && !health.llm_available && !currentRun?.degraded && (
              <div className="mb-4 flex items-center justify-between gap-4 rounded-[8px] border border-danger/25 bg-danger/[0.05] px-3 py-2 text-[11px] text-danger">
                <span>Groq is not configured. Add GROQ_API_KEY to enable the live agent demo.</span>
                <Badge variant="danger">agent unavailable</Badge>
              </div>
            )}
            {currentRun?.degraded && (
              <div className="mb-4 flex items-center justify-between gap-4 rounded-[8px] border border-danger/25 bg-danger/[0.05] px-3 py-2 text-[11px] text-danger">
                <span>{currentRun.degradations.at(-1) ?? 'A dependency failed and the run continued with reduced capabilities.'}</span>
                <Badge variant="danger">degraded</Badge>
              </div>
            )}
            <Outlet />
          </motion.main>
        </AnimatePresence>
      </div>
    </div>
  )
}

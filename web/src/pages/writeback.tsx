import {
  BookOpenText,
  Check,
  CircleX,
  Fingerprint,
  GitBranch,
  Link2,
  ListChecks,
  ShieldCheck,
} from 'lucide-react'
import { motion } from 'framer-motion'
import { useApp } from '../context/app-context'
import type { WritebackAction } from '../lib/types'
import { cn, motionEase } from '../lib/utils'
import { PageHeader } from '../components/page-header'
import { Badge } from '../components/ui/badge'
import { Card } from '../components/ui/card'
import { EmptyState } from '../components/ui/empty-state'
import { LoadingPanel } from '../components/ui/skeleton'

const groups: Array<{ title: string; detail: string; icon: typeof GitBranch; kinds: string[] }> = [
  { title: 'Incident', detail: 'Persist the changed contract in the graph.', icon: ShieldCheck, kinds: ['record_incident'] },
  { title: 'Documentation', detail: 'Carry repair context into the review package.', icon: BookOpenText, kinds: ['attach_migration_doc'] },
  { title: 'Audit', detail: 'Leave a reproducible graph process record.', icon: Fingerprint, kinds: ['record_repair_run'] },
]

const actionIcons: Record<string, typeof GitBranch> = {
  record_incident: ShieldCheck,
  attach_migration_doc: Link2,
  record_repair_run: Fingerprint,
}

function ActionRow({ action, index }: { action: WritebackAction; index: number }) {
  const Icon = actionIcons[action.kind] ?? ListChecks
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.03, ease: motionEase }}
      className="grid grid-cols-[34px_minmax(0,1fr)_auto] items-start gap-3 border-t border-border/70 px-4 py-3 first:border-t-0"
    >
      <div className={cn('flex size-8 items-center justify-center rounded-[8px] border', action.ok ? 'border-ok/25 bg-ok/[0.07] text-ok' : 'border-danger/25 bg-danger/[0.07] text-danger')}><Icon className="size-3.5" /></div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-text">{action.kind.split('_').map((word) => word[0].toUpperCase() + word.slice(1)).join(' ')}</span>
          <Badge variant={action.ok ? 'ok' : 'danger'} className="h-4 px-1.5 text-[7px]">{action.ok ? <Check className="size-2.5" /> : <CircleX className="size-2.5" />}{action.ok ? 'Complete' : 'Failed'}</Badge>
        </div>
        <p className="mb-1 mt-0.5 text-[10px] leading-4 text-text-dim">{action.error ?? action.detail}</p>
        <div className="truncate font-mono text-[8px] text-text-faint">{action.target_urn}</div>
      </div>
      <span className="mt-0.5 flex h-7 items-center rounded-[7px] border border-border-lit bg-surface-2 px-2.5 font-mono text-[9px] text-text-dim">{action.graph_url}</span>
    </motion.div>
  )
}

export function WritebackPage() {
  const { currentRun, currentRunLoading } = useApp()
  const actions = currentRun?.writeback ?? []
  const succeeded = actions.filter((action) => action.ok).length

  if (currentRunLoading) return <><PageHeader eyebrow="Graph handoff" title="FalkorDB Run State" detail="Loading persisted repair evidence." /><LoadingPanel rows={8} className="min-h-[520px]" /></>

  if (!actions.length) {
    return (
      <div>
        <PageHeader eyebrow="Graph handoff" title="FalkorDB Run State" detail="Repair evidence persists beyond the code change." />
        <EmptyState icon={currentRun?.status === 'running' ? ListChecks : ShieldCheck} title={currentRun?.status === 'running' ? 'Graph recording is waiting on validation' : 'No graph actions yet'} detail={currentRun?.status === 'running' ? 'The agent records the run only after the code and review package pass their gates.' : 'Complete a repair run to see incident, repair-run, and review evidence.'} />
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        eyebrow="Metadata handoff"
        title="FalkorDB Run State"
        detail="The repair closes the graph reasoning loop instead of ending at a code diff."
        actions={<Badge variant={succeeded === actions.length ? 'ok' : 'danger'}><Check className="size-3" />{succeeded}/{actions.length} actions complete</Badge>}
      />

      <div className="grid grid-cols-2 gap-4">
        {groups.map((group, groupIndex) => {
          const groupedActions = actions.filter((action) => group.kinds.includes(action.kind))
          const Icon = group.icon
          return (
            <Card key={group.title} className="overflow-hidden">
              <div className="flex items-center justify-between gap-4 border-b border-border bg-surface-2/35 px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex size-8 items-center justify-center rounded-[8px] border border-accent/20 bg-accent/[0.06] text-accent"><Icon className="size-3.5" /></div>
                  <div><h2 className="m-0 text-[12px] font-semibold text-text">{group.title}</h2><p className="m-0 mt-0.5 text-[9px] text-text-faint">{group.detail}</p></div>
                </div>
                <span className="font-mono text-[9px] text-text-faint">0{groupIndex + 1}</span>
              </div>
              {groupedActions.map((action, index) => <ActionRow key={`${group.title}-${action.kind}`} action={action} index={index} />)}
            </Card>
          )
        })}
      </div>

      <Card className="relative mt-4 overflow-hidden border-ok/25 bg-[linear-gradient(110deg,rgb(16_185_129/0.09),transparent_60%)] px-5 py-4">
        <div className="absolute -right-8 -top-16 size-44 rounded-full bg-ok/[0.07] blur-3xl" />
        <div className="relative flex items-center gap-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-[9px] border border-ok/30 bg-ok/10 text-ok shadow-[0_0_20px_rgb(16_185_129/0.12)]"><ShieldCheck className="size-[18px]" /></div>
          <div>
            <h2 className="m-0 text-[14px] font-semibold text-text">The next engineer — or the next agent — inherits this.</h2>
            <p className="m-0 mt-1 text-[10px] text-text-dim">The incident, review package, patched-asset links, and process run now live in the same authoritative graph used for reasoning.</p>
          </div>
        </div>
      </Card>
    </div>
  )
}

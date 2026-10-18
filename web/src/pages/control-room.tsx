import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  CircleDot,
  Clock3,
  ExternalLink,
  Github,
  GitFork,
  GitPullRequest,
  Network,
  ScanLine,
  ShieldCheck,
  TerminalSquare,
  TestTubeDiagonal,
  WandSparkles,
  Webhook,
  Workflow,
} from 'lucide-react'
import { motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useApp } from '../context/app-context'
import type { DriftEvent, RunEvent, RunPhase } from '../lib/types'
import { cn, formatDuration, motionEase, referenceCounts } from '../lib/utils'
import { PageHeader } from '../components/page-header'
import { Badge } from '../components/ui/badge'
import { Card } from '../components/ui/card'
import { EmptyState } from '../components/ui/empty-state'
import { LoadingPanel } from '../components/ui/skeleton'

const phaseDefinitions: Array<{ id: RunPhase; title: string; subtitle: string; icon: typeof ScanLine }> = [
  { id: 'detect', title: 'Detect schema drift', subtitle: 'Compare baseline with live FalkorDB schema', icon: ScanLine },
  { id: 'impact', title: 'Trace column lineage', subtitle: 'Classify the exact downstream blast radius', icon: Network },
  { id: 'codegen', title: 'Generate surgical patches', subtitle: 'Apply guarded AST transformations', icon: WandSparkles },
  { id: 'validate', title: 'Validate every reference', subtitle: 'Resolve columns against catalog evidence', icon: TestTubeDiagonal },
  { id: 'pr', title: 'Package pull request', subtitle: 'Assemble review-ready lineage evidence', icon: GitPullRequest },
  { id: 'writeback', title: 'Record graph state', subtitle: 'Persist incident, review, and audit evidence', icon: Workflow },
  { id: 'done', title: 'Repair complete', subtitle: 'Hand off a fully evidenced repair', icon: ShieldCheck },
]

function ToolChip({ event }: { event: RunEvent }) {
  const tool = typeof event.data.tool === 'string' ? event.data.tool : 'tool'
  const source = event.data.source === 'falkordb_mcp' ? 'falkordb' : 'repair_agent'
  const selector = event.data.selected_by === 'groq' ? 'Groq chose' : 'Safeguard'
  const transport = event.data.transport === 'mcp_stdio' ? 'MCP stdio' : 'local policy'
  const argumentsValue = event.data.arguments
  const argument = argumentsValue && typeof argumentsValue === 'object'
    ? Object.entries(argumentsValue as Record<string, unknown>)
      .slice(0, 2)
      .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
      .join(', ')
    : '…'
  return (
    <span className="inline-flex max-w-full items-center gap-2 rounded-[6px] border border-accent/20 bg-accent/[0.05] px-2.5 py-1.5 font-mono text-[10px] text-accent">
      <TerminalSquare className="size-3 shrink-0 text-accent" />
      <span className={cn('rounded-[3px] px-1 py-0.5 text-[7px] font-bold uppercase tracking-[0.08em]', event.data.selected_by === 'groq' ? 'bg-patch/15 text-patch' : 'bg-ok/10 text-ok')}>{selector}</span>
      <span className="rounded-[3px] bg-accent/10 px-1 py-0.5 text-[7px] font-bold uppercase tracking-[0.08em] text-accent">{transport}</span>
      <span className="truncate">{source}.{tool}({argument})</span>
    </span>
  )
}

function RunTimeline({
  events,
  status,
  completedStages,
  failedStage,
}: {
  events: RunEvent[]
  status: 'running' | 'succeeded' | 'failed'
  completedStages: string[]
  failedStage: string | null
}) {
  const [expanded, setExpanded] = useState<Set<RunPhase>>(new Set(['detect', 'impact']))
  const visibleEvents = events
    .filter((event) => event.level !== 'debug' || event.data.tool)
    .filter((event, index, filtered) => {
      if (index === 0) return true
      const previous = filtered[index - 1]
      return event.phase !== previous.phase
        || event.level !== previous.level
        || event.title !== previous.title
        || event.detail !== previous.detail
        || event.data.tool !== previous.data.tool
        || event.data.source !== previous.data.source
        || event.data.selected_by !== previous.data.selected_by
    })
  const lastPhase = visibleEvents.at(-1)?.phase
  const activeIndex = lastPhase ? phaseDefinitions.findIndex((phase) => phase.id === lastPhase) : -1
  const startedAt = events[0] ? new Date(events[0].ts).getTime() : Date.now()

  const toggle = (phase: RunPhase) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(phase)) next.delete(phase)
      else next.add(phase)
      return next
    })
  }

  return (
    <div className="relative">
      {phaseDefinitions.map((phase, index) => {
        const phaseEvents = visibleEvents.filter((event) => event.phase === phase.id)
        // Tick a phase only when the backend says it genuinely produced output. Inferring
        // completion from position meant a failed run showed green checks next to work that
        // never happened. 'done' has no artifact of its own, so it follows the run status.
        const completed = new Set(completedStages)
        const isDone =
          phase.id === 'done' ? status === 'succeeded' : completed.has(phase.id)
        const isFailed =
          status === 'failed' &&
          (failedStage ? phase.id === failedStage : index === activeIndex) &&
          !isDone
        const isActive = status === 'running' && index === activeIndex
        const hasActivity = phaseEvents.length > 0
        const first = phaseEvents[0] ? new Date(phaseEvents[0].ts).getTime() : startedAt
        const last = phaseEvents.at(-1) ? new Date(phaseEvents.at(-1)!.ts).getTime() : first
        const elapsed = Math.max(0, last - first)
        const Icon = phase.icon

        return (
          <motion.div
            key={phase.id}
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, delay: index * 0.03, ease: motionEase }}
            className="relative grid grid-cols-[32px_1fr] gap-3 pb-3 last:pb-0"
          >
            {index < phaseDefinitions.length - 1 && (
              <div className={cn('absolute left-[15px] top-7 h-[calc(100%-16px)] w-px', isDone ? 'bg-ok/35' : 'bg-border')} />
            )}
            <div className={cn(
              'relative z-10 flex size-8 items-center justify-center rounded-full border bg-surface transition-all duration-200',
              isDone && 'border-ok/35 bg-ok/8 text-ok',
              isActive && 'border-accent/40 bg-accent/8 text-accent',
              isFailed && 'border-danger/40 bg-danger/8 text-danger',
              !isDone && !isActive && !isFailed && 'border-border text-text-faint',
            )}>
              {isDone ? <Check className="size-3.5" /> : isActive ? <CircleDot className="size-3.5 animate-pulse" /> : <Icon className="size-3.5" />}
            </div>
            <div className={cn('min-w-0 rounded-[8px] border px-3 py-2.5', isActive ? 'border-accent/25 bg-accent/[0.045]' : 'border-transparent')}>
              <button className="flex w-full cursor-pointer items-center justify-between gap-4 border-0 bg-transparent p-0 text-left"
                type="button"
                onClick={() => toggle(phase.id)}
                disabled={!hasActivity}
              >
                <span className="min-w-0">
                  <span className={cn('block text-[12px] font-semibold', hasActivity || isActive || isDone ? 'text-text' : 'text-text-faint')}>{phase.title}</span>
                  <span className="block truncate text-[10px] text-text-faint">{phase.subtitle}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {hasActivity && <span className="font-mono text-[9px] text-text-faint">{formatDuration(elapsed)}</span>}
                  {hasActivity && <ChevronDown className={cn('size-3 text-text-faint transition-transform', expanded.has(phase.id) && 'rotate-180')} />}
                </span>
              </button>
              {expanded.has(phase.id) && phaseEvents.length > 0 && (
                <div className="mt-2 space-y-2 border-l border-border pl-3">
                  {phaseEvents.map((event) => (
                    <div key={event.seq} className="min-w-0">
                      {event.data.tool ? <ToolChip event={event} /> : (
                        <div>
                          <div className="text-[10px] font-medium text-text-dim">{event.title}</div>
                          {event.detail && <div className="mt-0.5 text-[10px] leading-4 text-text-faint">{event.detail}</div>}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </motion.div>
        )
      })}
    </div>
  )
}

export function ControlRoom() {
  const { currentRun, currentRunLoading, health, replay, resetVersion, streamConnected, streamError } = useApp()
  const [drifts, setDrifts] = useState<DriftEvent[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.drift()
      .then(setDrifts)
      .catch((caught) => setError(caught instanceof Error ? caught.message : 'Could not read the current graph state.'))
  }, [resetVersion])

  // Falling back to the run's own drift keeps the live view populated while a run is in
  // flight. In replay the same fallback would be a lie: it renders a historical drift as live for a
  // drift that was reset hours ago. A replayed run therefore only ever reports drift the
  // catalog is reporting right now, which for a clean catalog is none.
  const activeDrift = drifts[0] ?? (replay ? null : currentRun?.drift ?? null)
  const counts = useMemo(() => referenceCounts(currentRun), [currentRun])

  return (
    <div>
      <PageHeader
        eyebrow="Pull request automation"
        title="Repository investigations"
        detail="Connect a warehouse repository. Relink follows schema-changing pull requests from graph evidence to a reviewable repair."
        actions={currentRun?.status === 'running' ? (
          <Badge variant={streamConnected ? 'ok' : 'patch'}>
            <span className={cn('size-1 rounded-full', streamConnected ? 'bg-ok' : 'bg-patch')} />
            {streamConnected ? 'SSE live' : 'reconnecting'}
          </Badge>
        ) : undefined}
      />

      {error && <div role="alert" className="mb-4 rounded-[8px] border border-danger/30 bg-danger/5 px-4 py-2.5 text-[11px] text-danger">{error}</div>}
      {streamError && currentRun?.status === 'running' && <div className="mb-4 rounded-[8px] border border-patch/25 bg-patch/[0.05] px-4 py-2 text-[10px] text-patch">{streamError}</div>}

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="overflow-hidden border-accent/20">
          <div className="bg-accent px-6 py-5 text-white">
            <div className="flex items-center justify-between gap-4">
              <Badge className="border-white/25 bg-white/10 text-white"><Github className="size-3" /> GitHub-native workflow</Badge>
              <span className="text-[9px] font-semibold uppercase tracking-[0.12em] text-white/65">Designed for every schema PR</span>
            </div>
            <div className="mt-3">
              <h2 className="mb-2 mt-0 text-[23px] font-semibold tracking-[-0.02em]">GitHub is the trigger</h2>
              <p className="m-0 max-w-4xl text-[12px] leading-5 text-white/80">Open an issue or pull request in the connected repository. Relink receives the signed webhook, investigates connected data in FalkorDB, chooses evidence tools through MCP, and comments back with the proof.</p>
            </div>
          </div>
          <div className="grid grid-cols-1 divide-y divide-border md:grid-cols-4 md:divide-x md:divide-y-0">
            {[
              { icon: GitFork, step: '01', title: 'Connect a repository', text: 'Install Relink on a warehouse fork and index its code and metadata.' },
              { icon: Webhook, step: '02', title: 'Open a schema PR', text: 'A rename, retype, or drop starts an investigation automatically.' },
              { icon: Network, step: '03', title: 'Reason over the graph', text: 'Groq selects MCP tools while FalkorDB supplies multi-hop evidence.' },
              { icon: GitPullRequest, step: '04', title: 'Review the repair', text: 'Relink comments on the source PR and prepares a validated repair PR.' },
            ].map(({ icon: Icon, step, title, text }) => (
              <div key={step} className="min-h-36 p-4">
                <div className="flex items-center justify-between"><Icon className="size-4 text-accent" /><span className="font-mono text-[9px] text-text-faint">{step}</span></div>
                <h3 className="mb-1 mt-4 text-[11px] font-semibold text-text">{title}</h3>
                <p className="m-0 text-[9px] leading-4 text-text-dim">{text}</p>
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between gap-4 border-t border-border bg-surface-2 px-5 py-3">
            <div className="flex items-center gap-2 text-[10px] text-text-dim"><ShieldCheck className="size-3.5 text-ok" />No manual run control—the signed GitHub event starts the investigation.</div>
            <Badge variant={health?.github_app_configured ? 'ok' : 'patch'}>
              {health?.github_app_configured ? 'GitHub App webhook ready' : 'GitHub App credentials optional'}
            </Badge>
          </div>
        </Card>

        <Card className="p-5">
          <div className="flex items-center justify-between">
            <div className="flex size-10 items-center justify-center rounded-[9px] bg-[#24292f] text-white"><GitFork className="size-5" /></div>
            <Badge variant="accent">Connected repository</Badge>
          </div>
          <h2 className="mb-1 mt-4 text-[15px] font-semibold text-text">rohanmalhotracodes / jaffle_shop_duckdb</h2>
          <p className="m-0 text-[10px] leading-4 text-text-dim">Your connected dbt/DuckDB fork—the target used by both the local GitHub simulation and optional GitHub App delivery.</p>
          <div className="my-4 rounded-[8px] border border-border bg-surface-2 p-3">
            <div className="text-[8px] font-semibold uppercase tracking-[0.1em] text-text-faint">Suggested breakage</div>
            <div className="mt-1.5 font-mono text-[10px] text-text">raw_orders.order_date <ArrowRight className="mx-1 inline size-3 text-accent" /> ordered_at</div>
            <p className="mb-0 mt-2 text-[9px] leading-4 text-text-dim">Relink should repair the staging boundary with an alias and explain why downstream models remain insulated.</p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <a href="https://github.com/rohanmalhotracodes/jaffle_shop_duckdb/issues/new?title=Schema%20change%3A%20rename%20orders.order_placed_at%20to%20order_created_at&body=The%20upstream%20warehouse%20contract%20will%20rename%20shop_prod.raw.orders.order_placed_at%20to%20order_created_at.%20Relink%2C%20trace%20the%20downstream%20impact%20and%20prepare%20a%20safe%20repair." target="_blank" rel="noreferrer" className="inline-flex h-9 items-center justify-center gap-2 rounded-full bg-accent px-3 text-[10px] font-semibold text-white no-underline hover:bg-accent/90">Raise schema issue <ExternalLink className="size-3" /></a>
            <a href="https://github.com/rohanmalhotracodes/jaffle_shop_duckdb" target="_blank" rel="noreferrer" className="inline-flex h-9 items-center justify-center gap-2 rounded-full border border-border px-3 text-[10px] font-semibold text-text-dim no-underline hover:border-accent/30 hover:text-accent">View your fork <ArrowRight className="size-3" /></a>
          </div>
        </Card>
      </section>

      {activeDrift && (
        <div className="mt-4 flex items-center gap-3 rounded-[9px] border border-accent/25 bg-accent/[0.045] px-4 py-3 text-[11px] text-text shadow-[inset_3px_0_0_var(--accent)]">
          <CircleDot className="size-4 shrink-0 text-accent" />
          <span className="min-w-0 flex-1">
            Active incident: <code className="font-semibold text-accent">{activeDrift.dataset_name}.{activeDrift.old_column}</code>
            {activeDrift.new_column ? <> <ArrowRight className="mx-1 inline size-3" /> <code className="font-semibold text-accent">{activeDrift.new_column}</code></> : ' was dropped'}.
          </span>
          <Badge variant="accent">{Math.round(activeDrift.confidence * 100)}% confidence</Badge>
        </div>
      )}

      <div className={cn('mt-4 grid grid-cols-1 gap-4', currentRun && 'xl:grid-cols-[minmax(0,1fr)_390px]')}>
        {currentRun && <section className="min-w-0">
          {currentRun?.status === 'failed' && (
            <div className="mt-3 rounded-[10px] border border-danger/30 bg-danger/[0.04] px-4 py-3">
              <div className="flex items-center gap-2">
                <AlertTriangle className="size-3.5 shrink-0 text-danger" />
                <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-danger">
                  Repair run failed{currentRun.failed_stage ? ` at the ${currentRun.failed_stage} stage` : ''}
                </span>
              </div>
              <p className="m-0 mt-1.5 text-[11px] leading-5 text-text-dim">
                {currentRun.error ?? 'The run did not complete. Inspect the timeline below for the failing stage.'}
              </p>
              <p className="m-0 mt-1.5 text-[10px] leading-4 text-text-faint">
                The figures below describe an incomplete run and must not be read as a clean bill of health.
              </p>
            </div>
          )}

          {currentRun?.status === 'succeeded' && currentRun.pr?.state === 'no_changes_required' && (
            <div className="mt-3 rounded-[10px] border border-ok/30 bg-ok/[0.04] px-4 py-3">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-3.5 shrink-0 text-ok" />
                <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ok">No code changes required</span>
              </div>
              <p className="m-0 mt-1.5 text-[11px] leading-5 text-text-dim">
                FalkorDB shows that no mapped code references the changed column,
                so this repair had nothing to patch and no pull request was opened. A previous run
                may already have repaired it.
              </p>
            </div>
          )}

          {currentRun?.impact && (
            <div className="mt-3 grid grid-cols-4 gap-2">
              <SummaryCard label="Require patch" value={currentRun.impact.stats.requires_patch ?? 0} color="patch" />
              <SummaryCard label="No code change" value={currentRun.impact.stats.downstream_unaffected ?? 0} color="unaffected" />
              {/* Only claim the skips were CORRECT when the run actually succeeded. */}
              <SummaryCard
                label={currentRun.status === 'succeeded' ? 'Correctly skipped' : 'Skipped (unverified)'}
                value={currentRun.impact.stats.skipped ?? 0}
                color="skipped"
              />
              <SummaryCard label="References validated" value={`${counts.resolved}/${counts.total}`} color="ok" />
            </div>
          )}

          {currentRun?.agent_plan && (
            <div className={cn(
              'mt-3 rounded-[10px] border px-4 py-3',
              currentRun.agent_plan.verified ? 'border-ok/35 bg-ok/[0.06]' : 'border-patch/35 bg-patch/[0.06]',
            )}>
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  {currentRun.agent_plan.verified ? <ShieldCheck className="size-3.5 text-ok" /> : <AlertTriangle className="size-3.5 text-patch" />}
                  <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-text">
                    Groq action plan {currentRun.agent_plan.verified ? 'verified by FalkorDB policy' : 'rejected—safe fallback used'}
                  </span>
                </div>
                <Badge variant={currentRun.agent_plan.verified ? 'ok' : 'patch'}>{currentRun.agent_plan.decisions.length} decisions</Badge>
              </div>
              <p className="m-0 mt-1.5 text-[10px] leading-4 text-text-dim">
                {currentRun.agent_plan.verified
                  ? 'These graph-evidenced PATCH/SKIP actions drove the constrained AST executor.'
                  : currentRun.agent_plan.verification_errors.join(' ')}
              </p>
            </div>
          )}
        </section>}

        <Card className="min-h-[410px] p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="m-0 text-[13px] font-semibold text-text">Live execution</h2>
              <p className="m-0 mt-0.5 text-[10px] text-text-faint">Seven gated phases, streamed as they happen.</p>
            </div>
            {currentRun && <Badge variant={currentRun.status === 'succeeded' ? 'ok' : currentRun.status === 'failed' ? 'danger' : 'accent'}>{currentRun.events.length} events</Badge>}
          </div>
          {currentRunLoading ? <LoadingPanel rows={5} className="border-0 bg-transparent p-0 shadow-none" /> : currentRun ? (
            <RunTimeline events={currentRun.events} status={currentRun.status} completedStages={currentRun.completed_stages ?? []} failedStage={currentRun.failed_stage ?? null} />
          ) : (
            <EmptyState icon={Clock3} title="Ready for a live investigation" detail="Run the demo above to watch Groq choose FalkorDB MCP tools and stream every validation gate here." className="min-h-[330px] border-0 bg-transparent" />
          )}
        </Card>
      </div>
    </div>
  )
}

function SummaryCard({ label, value, color }: { label: string; value: string | number; color: 'patch' | 'unaffected' | 'skipped' | 'ok' }) {
  const colorClasses = { patch: 'text-patch', unaffected: 'text-unaffected', skipped: 'text-skipped', ok: 'text-ok' }
  return (
    <Card className="px-3 py-2.5 shadow-none">
      <div className={cn('font-mono text-[17px] font-semibold leading-none', colorClasses[color])}>{value}</div>
      <div className="mt-1.5 text-[9px] font-medium uppercase tracking-[0.08em] text-text-faint">{label}</div>
    </Card>
  )
}

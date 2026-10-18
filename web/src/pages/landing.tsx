import {
  ArrowRight,
  Code2,
  Database,
  GitBranch,
  Github,
  GitPullRequest,
  Network,
  Play,
  ShieldCheck,
  TerminalSquare,
  Waypoints,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '../lib/utils'

const stages = [
  { label: 'PR opened', value: '01' },
  { label: 'Graph traced', value: '02' },
  { label: 'Repair ready', value: '03' },
  { label: 'Review posted', value: '04' },
]

const stageCopy = [
  {
    eyebrow: 'GitHub event',
    title: 'orders.order_placed_at was renamed',
    body: 'Relink sees the changed contract and opens an investigation before the break reaches production.',
  },
  {
    eyebrow: 'FalkorDB evidence',
    title: 'Three hops, one alias boundary',
    body: 'Column paths reveal the exact model to patch and the downstream assets protected by an alias.',
  },
  {
    eyebrow: 'Guarded action',
    title: 'One surgical SQL edit',
    body: 'The graph-verified action becomes an AST edit, then every resulting column reference is validated.',
  },
  {
    eyebrow: 'Review handoff',
    title: 'The why arrives with the diff',
    body: 'The source PR gets a concise comment and reviewers get the full path, decisions, tests, and repair.',
  },
]

const stageActivity = [
  [
    'received pull_request webhook',
    'detected rename in raw.orders',
    'order_placed_at → order_created_at',
  ],
  [
    'mcp.inspect_schema() ✓',
    'mcp.trace_column_impact() ✓',
    'found 3 hops · 1 alias boundary',
  ],
  [
    'classified stg_orders as PATCH',
    'updated models/staging/stg_orders.sql',
    'mcp.validate_candidate_repair() ✓',
  ],
  [
    'validated 12/12 column references',
    'mcp.record_repair_result() ✓',
    'prepared review comment and repair PR',
  ],
]

const capabilities = [
  { icon: Waypoints, title: 'Multi-hop reasoning', text: 'Follow column lineage across models, jobs, and code—not just the first dependent.' },
  { icon: Database, title: 'Graph tools on every step', text: 'Schema inspection, path explanation, classification, and validation all query FalkorDB.' },
  { icon: TerminalSquare, title: 'Tool selection through MCP', text: 'Groq chooses the next graph tool while policy gates verify every proposed action.' },
  { icon: GitPullRequest, title: 'Actions from evidence', text: 'Only assets supported by graph paths move into the repair and review workflow.' },
]

const navLinkClass = 'relative py-2 text-[13px] font-semibold text-[#102944] no-underline transition-colors after:absolute after:bottom-0 after:left-0 after:h-[2px] after:w-full after:origin-left after:scale-x-0 after:bg-[#0a66c2] after:transition-transform after:duration-200 hover:text-[#0a66c2] hover:after:scale-x-100 focus-visible:after:scale-x-100'

export function LandingPage() {
  const [activeStage, setActiveStage] = useState(0)
  const [activityLine, setActivityLine] = useState(0)
  const [typedLength, setTypedLength] = useState(0)
  const [contactStatus, setContactStatus] = useState('')
  const [isScrolled, setIsScrolled] = useState(false)
  const stage = stageCopy[activeStage]

  useEffect(() => {
    const updateHeader = () => setIsScrolled(window.scrollY > 12)
    updateHeader()
    window.addEventListener('scroll', updateHeader, { passive: true })
    return () => window.removeEventListener('scroll', updateHeader)
  }, [])

  useEffect(() => {
    const lines = stageActivity[activeStage]
    const line = lines[activityLine]
    const isTyping = typedLength < line.length
    const isLastLine = activityLine === lines.length - 1
    const delay = isTyping ? 34 : isLastLine ? 1500 : 520

    const timer = window.setTimeout(() => {
      if (isTyping) {
        setTypedLength((length) => length + 1)
      } else if (!isLastLine) {
        setActivityLine((index) => index + 1)
        setTypedLength(0)
      } else {
        setActiveStage((index) => (index + 1) % stages.length)
        setActivityLine(0)
        setTypedLength(0)
      }
    }, delay)

    return () => window.clearTimeout(timer)
  }, [activeStage, activityLine, typedLength])

  const selectStage = (index: number) => {
    setActiveStage(index)
    setActivityLine(0)
    setTypedLength(0)
  }

  const openContactMessage = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const recipient = String(import.meta.env.VITE_CONTACT_EMAIL ?? '').trim()
    if (!recipient) {
      setContactStatus('Add VITE_CONTACT_EMAIL to enable contact delivery.')
      return
    }
    const fields = new FormData(event.currentTarget)
    const subject = String(fields.get('subject') ?? 'Relink question')
    const message = String(fields.get('message') ?? '')
    const sender = String(fields.get('email') ?? '')
    window.location.href = `mailto:${recipient}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(`${message}\n\nFrom: ${sender}`)}`
  }

  return (
    <div id="top" className="min-h-screen bg-[#fbfcfe] text-[#0b1f3a]">
      <header
        className={cn(
          'sticky top-0 z-50 border-b transition-[background-color,border-color,box-shadow] duration-300',
          isScrolled
            ? 'border-[#ccdeef] bg-[#f2f7fc]/95 shadow-[0_8px_24px_rgb(11_31_58/0.06)] backdrop-blur-xl'
            : 'border-transparent bg-[#f7fbff] shadow-none',
        )}
      >
        <div className="mx-auto flex h-[88px] max-w-[1440px] items-center px-6 lg:px-10">
          <Link to="/" className="flex items-center no-underline">
            <img src="/relink-logo.png" alt="Relink" className="h-11 w-auto max-w-[180px] object-contain object-left mix-blend-multiply" />
          </Link>
          <div className="ml-auto flex items-center gap-8 lg:gap-12">
            <nav aria-label="Marketing" className="hidden items-center gap-10 lg:flex">
              <a href="#about" className={navLinkClass}>About Us</a>
              <a href="#workflow" className={navLinkClass}>How It Works</a>
              <a href="#agents" className={navLinkClass}>Features</a>
              <a href="#contact" className={navLinkClass}>Contact Us</a>
            </nav>
            <Link to="/demo" className="inline-flex h-12 items-center gap-2 rounded-full bg-[#0a66c2] px-7 text-[13px] font-semibold text-white no-underline transition hover:bg-[#07549f]">
              View investigations <ArrowRight className="size-3.5" />
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section id="about" className="overflow-hidden border-b border-[#dce5ef] bg-[linear-gradient(180deg,#f7fbff_0%,#ffffff_72%)]">
          <div className="mx-auto grid max-w-[1320px] gap-16 px-6 pb-20 pt-20 lg:grid-cols-[0.92fr_1.08fr] lg:px-10 lg:pb-28 lg:pt-28">
            <div className="flex flex-col justify-center">
              <h1 className="m-0 max-w-[680px] text-[46px] font-bold leading-[1.02] tracking-[-0.055em] text-[#071a38] sm:text-[58px] lg:text-[68px]">
                Repair the whole dependency chain from one pull request.
              </h1>
              <p className="mb-0 mt-7 max-w-[620px] text-[17px] leading-7 text-[#4b6077]">
                Relink investigates schema changes through FalkorDB, lets Groq choose the evidence tools, and prepares a validated code repair reviewers can actually trust.
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Link to="/demo" className="inline-flex h-12 items-center gap-2 rounded-[8px] bg-[#0a66c2] px-6 text-[14px] font-semibold text-white no-underline shadow-[0_12px_28px_rgb(10_102_194/0.2)] transition hover:-translate-y-0.5 hover:bg-[#07549f]">
                  <Play className="size-4 fill-current" /> Open evidence dashboard
                </Link>
                <a href="#workflow" className="inline-flex h-12 items-center gap-2 rounded-[8px] border border-[#bdcad8] bg-white px-6 text-[14px] font-semibold text-[#17324f] no-underline hover:border-[#0a66c2] hover:text-[#0a66c2]">
                  See the workflow <ArrowRight className="size-4" />
                </a>
              </div>
            </div>

            <div className="relative">
              <div className="absolute -inset-10 -z-10 rounded-full bg-[#0a66c2]/[0.06] blur-3xl" />
              <div className="overflow-hidden rounded-[18px] border border-[#cbd8e5] bg-white shadow-[0_24px_70px_rgb(11_31_58/0.12)]">
                <div className="flex h-12 items-center justify-between border-b border-[#dce5ef] bg-[#f6f8fb] px-4">
                  <div className="flex items-center gap-2 text-[9px] font-semibold text-[#17324f]"><img src="/relink-favicon.png" alt="" className="size-5 rounded-[5px]" />Relink investigation</div>
                  <div className="flex items-center gap-2 font-mono text-[9px] text-[#667b91]"><Github className="size-3" />acme/warehouse · PR #184</div>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-[#b9d5f1] bg-white px-2 py-0.5 text-[8px] font-semibold text-[#0a66c2]"><span className="size-1.5 animate-pulse rounded-full bg-[#24a866]" />Running</span>
                </div>
                <div className="grid grid-cols-4 border-b border-[#dce5ef] bg-white">
                  {stages.map((item, index) => (
                    <button key={item.value} type="button" onClick={() => selectStage(index)} className={cn('border-0 border-r border-[#e6edf4] bg-white px-3 py-3 text-left last:border-r-0 hover:bg-[#f7fbff]', activeStage === index && 'bg-[#edf6ff]')}>
                      <span className={cn('block font-mono text-[8px]', activeStage === index ? 'text-[#0a66c2]' : 'text-[#8a9bad]')}>{item.value}</span>
                      <span className={cn('mt-1 block text-[9px] font-semibold', activeStage === index ? 'text-[#0a66c2]' : 'text-[#52687a]')}>{item.label}</span>
                    </button>
                  ))}
                </div>
                <div className="grid min-h-[370px] md:grid-cols-[0.92fr_1.08fr]">
                  <div className="border-b border-[#dce5ef] p-6 md:border-b-0 md:border-r">
                    <div className="text-[9px] font-bold uppercase tracking-[0.12em] text-[#0a66c2]">{stage.eyebrow}</div>
                    <h2 className="mb-2 mt-3 text-[22px] font-semibold leading-7 tracking-[-0.035em] text-[#102944]">{stage.title}</h2>
                    <p className="m-0 text-[11px] leading-5 text-[#61758a]">{stage.body}</p>
                    <div className="mt-6 rounded-[10px] border border-[#d6e3ef] bg-[#f7faff] p-3.5 font-mono text-[9px] text-[#29435d]">
                      <div className="mb-3 flex items-center gap-2 border-b border-[#dce7f1] pb-2 text-[8px] font-semibold uppercase tracking-[0.12em] text-[#55708b]">
                        <span className="size-1.5 animate-pulse rounded-full bg-[#24a866]" /> Live agent activity
                      </div>
                      <div className="min-h-[72px] space-y-2" aria-hidden="true">
                        {stageActivity[activeStage].map((line, index) => {
                          const isCurrent = index === activityLine
                          const isVisible = index <= activityLine
                          const output = index < activityLine ? line : isCurrent ? line.slice(0, typedLength) : ''
                          return (
                            <div key={line} className={cn('flex min-h-4 items-start gap-2 transition-opacity duration-300', isVisible ? 'opacity-100' : 'opacity-0')}>
                              <span className="text-[#0a66c2]">›</span>
                              <span className="break-words">
                                {output}
                                {isCurrent && <span className="ml-0.5 inline-block h-3 w-[1px] animate-pulse bg-[#0a66c2] align-middle" />}
                              </span>
                            </div>
                          )
                        })}
                      </div>
                      <span className="sr-only" aria-live="polite">{stageActivity[activeStage][activityLine]}</span>
                    </div>
                  </div>
                  <div className="bg-[#071a38] p-6 text-white">
                    <div className="flex items-center justify-between"><span className="text-[9px] font-bold uppercase tracking-[0.12em] text-white/55">Evidence path</span><Network className="size-4 text-[#69adf0]" /></div>
                    <div className="mt-5 space-y-0">
                      {[
                        ['raw.orders', 'order_placed_at'],
                        ['stg_orders', 'order_placed_at'],
                        ['fct_orders', 'order_date'],
                      ].map(([asset, column], index) => {
                        const isVisible = index === 0 || activeStage > 0
                        return (
                        <div key={asset} className={cn('transition-all duration-500', isVisible ? 'translate-y-0 opacity-100' : 'translate-y-2 opacity-20')}>
                          <div className="rounded-[9px] border border-white/15 bg-white/[0.06] p-3">
                            <div className="font-mono text-[9px] text-[#79b8f2]">{asset}</div>
                            <div className="mt-1 font-mono text-[11px] text-white">{column}</div>
                          </div>
                          {index < 2 && <div className="ml-5 h-4 border-l border-dashed border-[#69adf0]/50" />}
                        </div>
                        )
                      })}
                    </div>
                    <div className={cn('mt-4 flex items-center gap-1.5 border-t border-white/10 pt-3 text-[8px] font-medium text-[#8ce8bc] transition-all duration-500', activeStage > 0 ? 'translate-y-0 opacity-100' : 'translate-y-2 opacity-20')}><ShieldCheck className="size-3" />Alias boundary preserves the downstream contract</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="border-b border-[#dce5ef] bg-white">
          <div className="mx-auto grid max-w-[1320px] grid-cols-2 divide-x divide-[#dce5ef] px-6 md:grid-cols-4 lg:px-10">
            {[
              ['6', 'FalkorDB MCP tools'],
              ['7', 'gated repair phases'],
              ['1', 'authoritative runtime graph'],
              ['0', 'unverified references allowed'],
            ].map(([value, label]) => (
              <div key={label} className="px-5 py-7 text-center"><div className="font-mono text-[24px] font-semibold text-[#0a66c2]">{value}</div><div className="mt-1 text-[9px] font-semibold uppercase tracking-[0.1em] text-[#6c7f92]">{label}</div></div>
            ))}
          </div>
        </section>

        <section id="workflow" className="bg-white pb-24 pt-12 lg:pb-28 lg:pt-14">
          <div className="mx-auto max-w-[1440px] px-6 lg:px-10">
            <div className="max-w-[940px]">
              <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-[#0a66c2]">How it works</div>
              <h2 className="mb-4 mt-4 text-[42px] font-bold leading-[1.02] tracking-[-0.055em] text-[#071a38] md:text-[56px]">From changed schema to reviewed repair in three steps.</h2>
              <p className="m-0 max-w-[900px] text-[16px] leading-7 text-[#647a90]">Detect the contract change, investigate every connected path, and deliver a repair with the evidence attached.</p>
            </div>
            <div className="mt-10 grid gap-6 lg:grid-cols-3">
              {[
                { number: '01 / DETECT', icon: GitBranch, title: 'Find the changed contract', body: 'Relink reads the schema event and identifies the renamed, retyped, or dropped column before downstream failures spread.', className: 'border-[#bed7ee] bg-[#f8fbfe] text-[#0b1f3a]', iconClass: 'text-[#0a66c2]' },
                { number: '02 / INVESTIGATE', icon: Network, title: 'Trace the connected impact', body: 'FalkorDB follows multi-hop lineage while Groq selects MCP tools to classify direct patches and safe alias boundaries.', className: 'border-[#0b263f] bg-[#071f36] text-white', iconClass: 'text-[#67b4f5]' },
                { number: '03 / REPAIR', icon: Code2, title: 'Deliver the evidence', body: 'Guarded edits, graph-backed validation, lineage paths, and reviewer-ready GitHub prose arrive as one package.', className: 'border-[#61aaf0] bg-[#69b0f2] text-[#092038]', iconClass: 'text-[#092038]' },
              ].map(({ number, icon: Icon, title, body, className, iconClass }) => (
                <article key={number} className={cn('flex min-h-[340px] flex-col rounded-[32px] border p-8 lg:p-9', className)}>
                  <div className="font-mono text-[10px] font-semibold tracking-[0.08em] opacity-65">{number}</div>
                  <Icon className={cn('mt-12 size-12', iconClass)} strokeWidth={1.65} />
                  <h3 className="mb-3 mt-6 text-[22px] font-semibold tracking-[-0.035em]">{title}</h3>
                  <p className="m-0 text-[13px] leading-6 opacity-70">{body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="agents" className="bg-[#071a38] py-20 text-white lg:py-24">
          <div className="mx-auto max-w-[1180px] px-6 lg:px-10">
            <div className="grid gap-14 lg:grid-cols-[0.8fr_1.2fr]">
              <div>
                <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-[#69adf0]">Agents that act on connected data</div>
                <h2 className="mb-5 mt-4 text-[38px] font-bold leading-[1.07] tracking-[-0.045em] md:text-[48px]">The graph is not context. It is the operating surface.</h2>
                <p className="m-0 text-[14px] leading-7 text-white/65">Relink can read and write the graph on every step of the loop, select tools through MCP, and show the path behind every action it takes.</p>
                <Link to="/demo" className="mt-8 inline-flex h-12 items-center gap-2 rounded-[8px] bg-[#2f8de4] px-6 text-[13px] font-semibold text-white no-underline hover:bg-[#56a1e8]">Open the agent workspace <ArrowRight className="size-4" /></Link>
              </div>
              <div className="grid gap-px overflow-hidden rounded-[14px] border border-white/15 bg-white/15 sm:grid-cols-2">
                {capabilities.map(({ icon: Icon, title, text }) => (
                  <div key={title} className="bg-[#0b2342] p-6 lg:p-7">
                    <Icon className="size-5 text-[#69adf0]" />
                    <h3 className="mb-2 mt-5 text-[15px] font-semibold">{title}</h3>
                    <p className="m-0 text-[11px] leading-5 text-white/60">{text}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section id="contact" className="border-t border-[#d6e3ef] bg-white py-20 lg:py-24">
          <div className="mx-auto grid max-w-[1440px] gap-16 px-6 lg:grid-cols-[0.75fr_1.25fr] lg:px-10">
            <div className="flex flex-col justify-start">
              <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-[#0a66c2]">Contact us</div>
              <h2 className="mb-5 mt-4 text-[54px] font-bold leading-none tracking-[-0.055em] text-[#071a38] md:text-[72px]">Let’s talk.</h2>
              <p className="m-0 max-w-[540px] text-[17px] leading-7 text-[#647a90]">Share your question, feedback, or the warehouse workflow you want Relink to investigate.</p>
            </div>
            <form onSubmit={openContactMessage} className="grid gap-5 sm:grid-cols-2">
              <label className="block text-[12px] font-semibold text-[#17324f]">Your email<input required name="email" type="email" placeholder="you@example.com" className="mt-2 h-14 w-full rounded-[18px] border border-[#c7daec] bg-white px-4 text-[13px] font-normal outline-none focus:border-[#0a66c2] focus:ring-2 focus:ring-[#0a66c2]/10" /></label>
              <label className="block text-[12px] font-semibold text-[#17324f]">Subject<input required name="subject" placeholder="How can we help?" className="mt-2 h-14 w-full rounded-[18px] border border-[#c7daec] bg-white px-4 text-[13px] font-normal outline-none focus:border-[#0a66c2] focus:ring-2 focus:ring-[#0a66c2]/10" /></label>
              <label className="block text-[12px] font-semibold text-[#17324f] sm:col-span-2">Message<textarea required name="message" rows={6} placeholder="Tell us about your project, question, or feedback." className="mt-2 w-full resize-y rounded-[18px] border border-[#c7daec] bg-white p-4 text-[13px] font-normal outline-none focus:border-[#0a66c2] focus:ring-2 focus:ring-[#0a66c2]/10" /></label>
              <button type="submit" className="h-16 cursor-pointer rounded-full border-0 bg-[#0a66c2] text-[14px] font-semibold text-white hover:bg-[#07549f] sm:col-span-2">Send message</button>
              {contactStatus && <p role="status" className="m-0 text-[10px] text-[#a34f00] sm:col-span-2">{contactStatus}</p>}
            </form>
          </div>
        </section>
      </main>

      <footer className="border-t border-[#d6e3ef] bg-white">
        <div className="mx-auto flex max-w-[1440px] flex-col justify-between gap-8 px-6 py-10 md:flex-row md:items-end lg:px-10">
          <div><img src="/relink-logo.png" alt="Relink" className="h-10 w-auto max-w-[170px] object-contain object-left" /><p className="mb-0 mt-3 font-mono text-[9px] text-[#71869a]">© {new Date().getFullYear()} Relink. All rights reserved.</p></div>
          <div className="flex flex-wrap items-center gap-7">
            <span title="Link coming soon" className="text-[11px] font-semibold text-[#17324f]">Privacy Policy</span>
            <span title="Link coming soon" className="text-[11px] font-semibold text-[#17324f]">YouTube</span>
            <a href="#contact" className="text-[11px] font-semibold text-[#17324f] no-underline hover:text-[#0a66c2]">Contact us</a>
            <a href="https://github.com/rohanmalhotracodes/jaffle_shop_duckdb" target="_blank" rel="noreferrer" className="text-[11px] font-semibold text-[#17324f] no-underline hover:text-[#0a66c2]">GitHub</a>
            <a href="#top" className="text-[11px] font-semibold text-[#17324f] no-underline hover:text-[#0a66c2]">Top ↑</a>
          </div>
        </div>
      </footer>
    </div>
  )
}

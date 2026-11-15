import { useEffect, useId, useRef, useState } from 'react'
import { ArrowUpRight, ChartNoAxesCombined } from 'lucide-react'
import { api, type Briefing, type DependencyGraph, type InsightsData, type RiskItem } from './api'
import { Button, EmptyState, ErrorMessage, formatDate } from './components'
import { statusLabel } from './lifecycle'

const riskLabels: Record<RiskItem['level'], string> = {
  high: 'High risk', watch: 'Watch', on_track: 'On track', insufficient: 'Insufficient information', closed: 'Closed',
}

function showGraph(section: HTMLElement | null) {
  section?.focus({ preventScroll: true })
  section?.scrollIntoView({ block: 'start' })
}

export function Insights({ focus }: { focus?: number }) {
  const [days, setDays] = useState(90)
  const [owner, setOwner] = useState('')
  const [insights, setInsights] = useState<InsightsData>()
  const [graphId, setGraphId] = useState<number | undefined>(focus)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const graphSection = useRef<HTMLElement>(null)
  const openedGraph = useRef(false)

  useEffect(() => {
    let current = true
    setError('')
    setLoading(true)
    api.insights(days, owner).then(data => { if (current) setInsights(data) })
      .catch((failure: Error) => { if (current) setError(failure.message) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [days, owner])

  useEffect(() => {
    if (focus === undefined || openedGraph.current || !graphSection.current) return
    openedGraph.current = true
    showGraph(graphSection.current)
  }, [focus, insights, loading])

  const selected = graphId ?? insights?.graph_options[0]?.id
  return <>
    <header className="page-heading">
      <div><h1>Insights</h1><p>See what needs attention, how commitments are linked, and what changed over time.</p></div>
      <Button variant="outline" disabled={loading || Boolean(error)} onClick={() => showGraph(graphSection.current)}>
        Jump to dependency graph
      </Button>
    </header>
    <section className="panel form-panel">
      <div className="memory-filters">
        <label>Owner<select aria-label="Insights owner" value={owner} onChange={event => { setOwner(event.target.value); setGraphId(undefined) }}>
          <option value="">All owners</option>
          {insights?.owners.map(name => <option key={name}>{name}</option>)}
        </select></label>
        <label>History window<select aria-label="History window" value={days} onChange={event => setDays(Number(event.target.value))}>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
          <option value={365}>Last 365 days</option>
        </select></label>
      </div>
    </section>
    <div className="memory-stack">
      <ErrorMessage error={error} />
      {loading && <p role="status">Loading insights…</p>}
      {!loading && !error && insights && <>
        <DailyBriefing key={owner} owner={owner} />
        <section className="panel memory-card">
          <h2>Risk signals</h2>
          <p>Based on confirmed due dates, saved progress, and prerequisites.</p>
          {!insights.risks.length && <p>There are no open commitments in this view.</p>}
          {insights.risk_total > insights.risks.length && <p>Showing {insights.risks.length} of {insights.risk_total} open commitments.</p>}
          {insights.risks.map(item => <RiskRow key={item.id} item={item} />)}
        </section>
        <section className="panel memory-card" ref={graphSection} tabIndex={-1} aria-labelledby="dependency-graph-title">
          <h2 id="dependency-graph-title">Dependency graph</h2>
          <p>Arrows point from prerequisites to the commitments that depend on them.</p>
          {selected ? <>
            <div className="form-panel insight-graph-picker"><label>Commitment<select aria-label="Graph commitment" value={selected} onChange={event => setGraphId(Number(event.target.value))}>
              {!insights.graph_options.some(item => item.id === selected) && <option value={selected}>Selected commitment</option>}
              {insights.graph_options.map(item => <option key={item.id} value={item.id}>{item.task} · {item.meeting_title}</option>)}
            </select></label></div>
            <Graph key={selected} id={selected} />
          </> : <p>Confirm a commitment in the <a className="text-link" href="#/ledger">Promise Ledger</a> to view its dependencies.</p>}
        </section>
        <Historical data={insights} />
      </>}
    </div>
  </>
}

function RiskRow({ item }: { item: RiskItem }) {
  return <article className="insight-row">
    <div className="memory-card-title">
      <a className="text-link" href={'#/ledger/' + item.id}>{item.task} <ArrowUpRight size={14} /></a>
      <span className={item.level === 'high' ? 'badge badge-overdue' : 'badge'}>{riskLabels[item.level]}
        {item.score !== null && ' · ' + item.score + '/100'}</span>
    </div>
    <p>{item.owner ?? 'Owner unspecified'} · {item.meeting_title}{item.due_date && ' · Due ' + formatDate(item.due_date)}</p>
    {item.reasons.map(reason => <p key={reason}>{reason}</p>)}
  </article>
}

function DailyBriefing({ owner }: { owner: string }) {
  const [result, setResult] = useState<Briefing>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function generate() {
    setBusy(true)
    setError('')
    try { setResult(await api.briefing(owner)) }
    catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  return <section className="panel memory-card">
    <div className="panel-heading insight-heading">
      <div><h2>Daily accountability briefing</h2>{result && <p>{formatDate(result.date)} · {result.mode === 'ai' ? 'AI-assisted priorities' : 'Ledger-ranked priorities'}</p>}</div>
      <Button disabled={busy} onClick={() => void generate()}>{busy ? 'Preparing briefing…' : 'Generate briefing'}</Button>
    </div>
    <ErrorMessage error={error} />
    {result && <>
      <p>{result.counts.open} open · {result.counts.overdue} overdue · {result.counts.due_today} due today · {result.counts.high_risk} high risk · {result.counts.undated} without a date</p>
      {result.items.length ? result.items.map(item => <RiskRow key={item.id} item={item} />) : <p>No priorities to highlight today.</p>}
    </>}
  </section>
}

function Graph({ id }: { id: number }) {
  const [data, setData] = useState<DependencyGraph>()
  const [error, setError] = useState('')
  const marker = useId().replace(/[^a-z0-9_-]/gi, '')

  useEffect(() => {
    let current = true
    api.dependencyGraph(id).then(value => { if (current) setData(value) })
      .catch((failure: Error) => { if (current) setError(failure.message) })
    return () => { current = false }
  }, [id])

  if (error) return <ErrorMessage error={error} />
  if (!data) return <p role="status">Loading dependencies…</p>
  if (!data.nodes.length) return <p>This commitment is no longer available.</p>

  const levels = new Map<number, number>()
  const points = new Map(data.nodes.map(node => {
    const row = levels.get(node.level) ?? 0
    levels.set(node.level, row + 1)
    return [node.id, { x: 12 + node.level * 242, y: 12 + row * 76 }]
  }))
  const width = 24 + Math.max(...data.nodes.map(node => node.level)) * 242 + 192
  const height = Math.max(...levels.values()) * 76 + 12
  return <>
    {!data.edges.length && <p>This commitment has no linked dependencies. Open its node and use <strong>Manage commitment → Find a prerequisite</strong> to add one.</p>}
    {data.truncated && <p>Showing the closest {data.nodes.length} linked commitments.</p>}
    <div className="insight-graph" tabIndex={0} role="region" aria-label="Scrollable dependency graph">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Commitment dependencies">
        <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" className="graph-arrow" />
        </marker></defs>
        {data.edges.map(({ source, target }) => {
          const start = points.get(source)!
          const end = points.get(target)!
          const fromX = start.x + 192
          const mid = (fromX + end.x) / 2
          return <path key={source + ':' + target} className="graph-edge" markerEnd={`url(#${marker})`}
            d={`M ${fromX} ${start.y + 29} C ${mid} ${start.y + 29}, ${mid} ${end.y + 29}, ${end.x - 4} ${end.y + 29}`} />
        })}
        {data.nodes.map(node => {
          const point = points.get(node.id)!
          return <a href={'#/ledger/' + node.id} key={node.id} className={node.id === data.root ? 'graph-node graph-current' : 'graph-node'}
            aria-label={`${node.task}, ${statusLabel(node.status)}, open in Promise Ledger`}>
            <title>{node.task} · {node.meeting_title}</title>
            <rect x={point.x} y={point.y} width="192" height="57" rx="8" />
            <text x={point.x + 10} y={point.y + 22}>{node.task.length > 24 ? node.task.slice(0, 23) + '…' : node.task}</text>
            <text x={point.x + 10} y={point.y + 43} className="graph-status">{statusLabel(node.status)}</text>
          </a>
        })}
      </svg>
    </div>
  </>
}

function Historical({ data }: { data: InsightsData }) {
  const metrics = data.metrics
  const total = metrics.total ?? 0
  const completed = metrics.completed ?? 0
  const timed = metrics.timed_completions ?? 0
  return <>
    <section className="panel memory-card">
      <h2>Commitment history</h2>
      <p>Meetings from {formatDate(data.from)} through {formatDate(data.through)}.</p>
      {total ? <>
        <dl className="insight-metrics">
          <div><dt>Confirmed</dt><dd>{total}</dd></div>
          <div><dt>Completed</dt><dd>{completed}</dd></div>
          <div><dt>Completed on time</dt><dd>{timed ? (metrics.on_time ?? 0) + ' of ' + timed : '—'}</dd></div>
          <div><dt>Evidence supports completion</dt><dd>{metrics.verified_completions ?? 0}</dd></div>
          <div><dt>Deadline changes</dt><dd>{metrics.deadline_changes ?? 0}</dd></div>
        </dl>
        <p>On-time counts use the due date recorded when the commitment was completed.</p>
        <div className="insight-table-wrap"><table className="insight-table">
          <thead><tr><th scope="col">Meeting week</th><th scope="col">Confirmed</th><th scope="col">Completed</th><th scope="col">Deadline changes</th></tr></thead>
          <tbody>{data.weeks.map(row => <tr key={row.week}>
            <th scope="row">{formatDate(row.week)}</th><td>{row.confirmed}</td><td>{row.completed ?? 0}</td><td>{row.deadline_changes}</td>
          </tr>)}</tbody>
        </table></div>
      </> : <EmptyState icon={<ChartNoAxesCombined size={25} />} title="No commitments in this period"><p>Choose a wider history window or confirm commitments in a meeting.</p></EmptyState>}
    </section>
    <section className="panel memory-card">
      <h2>Recurring commitments across meetings</h2>
      {data.recurring.length ? data.recurring.map(group => <details className="memory-matches insight-row" key={group.owner + ':' + group.task}>
        <summary>{group.task} · {group.owner} · {group.meeting_count} meetings</summary>
        {group.commitments.map(item => <div key={item.id}>
          <a className="text-link" href={'#/ledger/' + item.id}>{item.meeting_title} <ArrowUpRight size={14} /></a>
          <p>{formatDate(item.meeting_date)} · {statusLabel(item.status)}</p>
        </div>)}
      </details>) : <p>No repeated owner and task pairs in this history window.</p>}
    </section>
  </>
}

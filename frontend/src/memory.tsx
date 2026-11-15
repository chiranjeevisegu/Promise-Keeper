import { useEffect, useState, type FormEvent } from 'react'
import { ArrowUpRight, BookCheck, Compass, Link2, Search } from 'lucide-react'
import { api, type Evidence, type LedgerPage, type Meeting, type MemoryResult, type PromiseRecord, type RelatedPromises, type Scope, type User } from './api'
import { Button, EmptyState, ErrorMessage, formatDate, today } from './components'
import { FollowUpDraft } from './follow-up'
import { EvidenceReviewPanel } from './evidence-review'
import { LifecyclePanel, statusLabel, workflowStatuses } from './lifecycle'

const statuses = [...workflowStatuses, 'overdue']
type MemoryTurn = { query: string; mode: 'search' | 'ask'; result: MemoryResult }

function MeetingSelect({ meetings, value, onChange }: { meetings: Meeting[]; value?: number; onChange: (value?: number) => void }) {
  return <label>Meeting<select aria-label="Meeting" value={value ?? ''} onChange={event => onChange(Number(event.target.value) || undefined)}>
    <option value="">All meetings</option>
    {meetings.map(meeting => <option key={meeting.id} value={meeting.id}>{meeting.title} · {formatDate(meeting.date)}</option>)}
  </select></label>
}

function Source({ item, quote = item.quote }: { item: Evidence | PromiseRecord; quote?: string }) {
  return <div className="memory-source">
    <blockquote>{quote}</blockquote>
    <small>{item.speaker ?? 'Unknown speaker'} · {item.start === null ? 'Manual transcript' : item.start.toFixed(2) + ' s'}</small>
    <a className="text-link" href={'#/meetings/' + item.meeting_id}>{item.meeting_title} · {formatDate(item.meeting_date)} <ArrowUpRight size={14} /></a>
  </div>
}

export function PromiseLedger({ meetings, users, selectedId }: { meetings: Meeting[]; users: User[]; selectedId?: number }) {
  const [filters, setFilters] = useState<Scope>({})
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<LedgerPage>()
  const [failure, setFailure] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let current = true
    api.promises({ ...filters, offset, promise_id: selectedId }).then(result => { if (current) setPage(result) })
      .catch((error: Error) => { if (current) setFailure(error.message) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [filters, offset, revision, selectedId])

  function apply(next: Scope, nextOffset = 0) {
    if (next === filters && nextOffset === offset) return
    setFailure('')
    setLoading(true)
    setFilters(next)
    setOffset(nextOffset)
  }

  return <>
    <header className="page-heading"><div><h1>Promise Ledger</h1><p>Confirmed commitments and their original evidence.</p></div>
      {selectedId !== undefined && <a className="button button-outline" href="#/ledger">View all commitments</a>}
    </header>
    {selectedId === undefined && <section className="panel form-panel">
      <div className="memory-filters">
        <label>Owner<select aria-label="Owner" value={filters.owner ?? ''} onChange={event => apply({ ...filters, owner: event.target.value || undefined })}>
          <option value="">All owners</option>
          {page?.owners.map(owner => <option key={owner}>{owner}</option>)}
        </select></label>
        <label>Status<select aria-label="Status" value={filters.status ?? ''} onChange={event => apply({ ...filters, status: event.target.value || undefined })}>
          <option value="">All statuses</option>
          {statuses.map(status => <option key={status} value={status}>{statusLabel(status)}</option>)}
        </select></label>
        <MeetingSelect meetings={meetings} value={filters.meeting_id} onChange={meeting_id => apply({ ...filters, meeting_id })} />
        <form className="memory-inline memory-deadline-filter" onSubmit={event => {
          event.preventDefault()
          const deadline = String(new FormData(event.currentTarget).get('deadline')).trim()
          apply({ ...filters, deadline: deadline || undefined })
        }}>
          <label>Deadline contains<input name="deadline" type="search" maxLength={150} placeholder="e.g. Friday" /></label>
          <Button type="submit" variant="outline">Apply</Button>
        </form>
      </div>
    </section>}
    <div className="memory-stack">
      <ErrorMessage error={failure} />
      {loading && <p role="status">Loading promises…</p>}
      {!loading && !failure && page && (page.items.length ? <>
        <p className="memory-count">{page.total} {page.total === 1 ? 'commitment' : 'commitments'}</p>
        {page.items.map(item => <PromiseCard key={item.id} item={item} users={users} onSaved={() => setRevision(value => value + 1)} />)}
        {(offset > 0 || offset + page.items.length < page.total) && <nav className="memory-inline" aria-label="Promise pages">
          <Button variant="outline" disabled={!offset} onClick={() => apply(filters, Math.max(0, offset - 30))}>Previous</Button>
          <Button variant="outline" disabled={offset + page.items.length >= page.total} onClick={() => apply(filters, offset + 30)}>Next</Button>
        </nav>}
      </> : <section className="panel"><EmptyState icon={<BookCheck size={25} />} title="No matching commitments">
        <p>Approve commitments in a meeting or change your filters.</p>
        <a className="button button-outline" href="#/meetings">View meetings</a>
      </EmptyState></section>)}
    </div>
  </>
}

function PromiseCard({ item, users, onSaved }: { item: PromiseRecord; users: User[]; onSaved: () => void }) {
  const [show, setShow] = useState(false)
  const [related, setRelated] = useState<RelatedPromises>()
  const [failure, setFailure] = useState('')

  async function toggle() {
    setShow(!show)
    if (show || related) return
    setFailure('')
    try { setRelated(await api.relatedPromises(item.id)) }
    catch (error) { setFailure((error as Error).message) }
  }

  return <article className="panel memory-card">
    <div className="memory-card-title"><h2>{item.task}</h2><span className={item.status === 'overdue' ? 'badge badge-overdue' : 'badge'}>{statusLabel(item.status)}</span></div>
    <p className="memory-meta">{item.owner ?? 'Owner unspecified'}{item.recipient && ' → ' + item.recipient}
      {item.due_date ? ' · Due ' + formatDate(item.due_date) : item.deadline && ' · Deadline mentioned: ' + item.deadline}</p>
    <Source item={item} />
    <LifecyclePanel id={item.id} users={users} onSaved={onSaved} />
    {item.workflow_status === 'completed' && <EvidenceReviewPanel key={item.lifecycle_version} id={item.id} users={users} />}
    {item.workflow_status !== 'completed' && item.workflow_status !== 'cancelled' && <FollowUpDraft id={item.id} />}
    <a className="text-link" href={'#/insights/' + item.id}>View dependency graph <ArrowUpRight size={14} /></a>
    <Button variant="ghost" aria-expanded={show} onClick={() => void toggle()}><Link2 size={16} /> Related commitments</Button>
    {show && <div className="memory-stack">
      <ErrorMessage error={failure} />
      {!related && !failure && <p role="status">Finding related commitments…</p>}
      {related && !related.duplicates.length && !related.related.length && <p>No matching commitments in other meetings.</p>}
      {related?.duplicates.map(duplicate => <div key={duplicate.id}><h3>{duplicate.task} <span className="badge">Possible duplicate</span></h3><Source item={duplicate} /></div>)}
      {related?.related.map(match => <div key={match.id}><h3>{match.task}</h3><Source item={match} /></div>)}
    </div>}
  </article>
}

export function MemoryExplorer({ meetings, users }: { meetings: Meeting[]; users: User[] }) {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<Scope>({})
  const [profile, setProfile] = useState<number | undefined>(users.length === 1 ? users[0].id : undefined)
  const [turns, setTurns] = useState<MemoryTurn[]>([])
  const [failure, setFailure] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!query.trim() || busy) return
    const mode = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value') === 'search' ? 'search' : 'ask'
    const question = query.trim()
    setBusy(true)
    setFailure('')
    try {
      const lastQuestion = turns.find(turn => turn.mode === 'ask')?.query
      const result = await api.memory(mode, { ...scope, query: question, as_of: today(), profile_id: profile,
        previous_question: mode === 'ask' ? lastQuestion : undefined })
      const turn: MemoryTurn = { query: question, mode, result }
      setTurns(history => [turn, ...history].slice(0, 6))
      setQuery('')
    } catch (error) { setFailure((error as Error).message) }
    finally { setBusy(false) }
  }

  return <>
    <header className="page-heading"><div><h1>Memory Explorer</h1><p>Search meeting history or ask a question with transcript citations.</p></div></header>
    <section className="panel form-panel">
      <form onSubmit={event => void submit(event)}>
        <fieldset disabled={busy} className="memory-form">
          <label>Question or search terms<input required maxLength={500} placeholder="What did I promise last week?" value={query} onChange={event => setQuery(event.target.value)} /></label>
          <div className="memory-filters">
            <MeetingSelect meetings={meetings} value={scope.meeting_id} onChange={meeting_id => setScope({ ...scope, meeting_id })} />
            <label>My profile<select aria-label="My profile" value={profile ?? ''} onChange={event => setProfile(Number(event.target.value) || undefined)}>
              <option value="">Choose a profile</option>
              {users.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}
            </select></label>
            <label className="check-label"><input type="checkbox" checked={scope.promises_only ?? false}
              onChange={event => setScope({ ...scope, promises_only: event.target.checked })} /> Confirmed commitments only</label>
          </div>
          <div className="memory-inline">
            <Button type="submit" value="ask"><Compass size={16} /> Ask</Button>
            <Button type="submit" value="search" variant="outline"><Search size={16} /> Search</Button>
            {turns.length > 0 && <Button variant="ghost" onClick={() => setTurns([])}>Clear conversation</Button>}
          </div>
        </fieldset>
      </form>
    </section>
    <div className="memory-stack">
      <ErrorMessage error={failure} />
      {busy && <p role="status">Searching saved meetings…</p>}
      {turns.map(({ query, result }, index) => <section className="panel memory-card" key={index + ':' + query}>
        <h2>{query}</h2>
        {result.retrieval === 'keyword' && <p>Semantic search is unavailable. Showing keyword matches.</p>}
        {result.status === 'insufficient' && <p>No evidence was sufficient to answer this query.</p>}
        {result.status === 'generation_unavailable' && <p>Ollama was unavailable. You can still read the retrieved sources.</p>}
        {result.status === 'ledger' && <p>{result.total} matching {result.total === 1 ? 'commitment' : 'commitments'}{result.total > result.matches.length && ' · Open the ledger for the full list'}</p>}
        {result.answer.length > 0 && <div className="memory-stack">
          <h3>Answer from transcript evidence</h3>
          {result.answer.map(passage => {
            const item = result.matches.find(match => match.id === passage.source_id)
            return item && <Source key={passage.source_id + passage.quote} item={item} quote={passage.quote} />
          })}
        </div>}
        {result.matches.length > 0 && <details className="memory-matches" open={result.answer.length === 0}>
          <summary>{result.status === 'ledger' ? 'Confirmed commitments' : 'Retrieved sources'}</summary>
          {result.matches.map(item => <div key={item.id}>
            {item.task && <><h3>{item.task}</h3><p>{item.owner ?? 'Owner unspecified'}{item.status && ' · ' + statusLabel(item.status)}
              {item.due_date ? ' · Due ' + formatDate(item.due_date) : item.deadline && ' · Deadline mentioned: ' + item.deadline}</p></>}
            <Source item={item} />
          </div>)}
        </details>}
      </section>)}
    </div>
  </>
}

import { useState, type FormEvent } from 'react'
import { api, type Dependency, type Lifecycle, type User, type WorkflowStatus } from './api'
import { Button, ErrorMessage, formatDate } from './components'

export const workflowStatuses: WorkflowStatus[] = ['pending', 'in_progress', 'unverified', 'completed', 'cancelled']
export const statusLabel = (value: string) => value.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())

export function LifecyclePanel({ id, users, onSaved }: { id: number; users: User[]; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<Lifecycle>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  async function reload() {
    setError('')
    setSaved(false)
    setLoading(true)
    try { setData(await api.lifecycle(id)) }
    catch (failure) { setError((failure as Error).message) }
    finally { setLoading(false) }
  }

  return <div className="lifecycle-panel">
    <Button variant="outline" aria-expanded={open} onClick={() => {
      setOpen(!open)
      if (!open) void reload()
    }}>Manage commitment</Button>
    {open && <>
      <ErrorMessage error={error} />
      {saved && <p role="status">Lifecycle update saved.</p>}
      {loading && <p role="status">Loading commitment lifecycle…</p>}
      {!loading && data && <LifecycleEditor key={data.promise.lifecycle_version} data={data} users={users}
        onReload={() => void reload()} onSave={next => { setData(next); setSaved(true); onSaved() }} />}
    </>}
  </div>
}

function LifecycleEditor({ data, users, onSave, onReload }: {
  data: Lifecycle; users: User[]; onSave: (value: Lifecycle) => void; onReload: () => void
}) {
  const { promise } = data
  const [actor, setActor] = useState(users.length === 1 ? users[0].id : 0)
  const [status, setStatus] = useState(promise.workflow_status)
  const [due, setDue] = useState(promise.due_date ?? '')
  const [dependencies, setDependencies] = useState(data.prerequisites)
  const [search, setSearch] = useState('')
  const [options, setOptions] = useState<Dependency[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [evidence, setEvidence] = useState('')
  const [url, setUrl] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [failure, setFailure] = useState('')
  const [busy, setBusy] = useState(false)

  async function findDependencies() {
    setSearching(true)
    setFailure('')
    try { setOptions(await api.dependencyOptions(promise.id, search)) }
    catch (error) { setFailure((error as Error).message) }
    finally { setSearching(false) }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFailure('')
    setBusy(true)
    try {
      onSave(await api.saveLifecycle(promise.id, {
        actor_id: actor, status, due_date: due || null, version: promise.lifecycle_version,
        prerequisites: dependencies.map(item => item.id),
        evidence: status === 'completed' ? evidence.trim() || null : null,
        evidence_url: status === 'completed' ? url.trim() || null : null,
        confirm_completion: confirmed,
      }))
    } catch (error) { setFailure((error as Error).message) }
    finally { setBusy(false) }
  }

  const available = options?.filter(option => !dependencies.some(item => item.id === option.id))
  const blocked = status === 'completed' && dependencies.some(item => item.status !== 'completed')
  const needsEvidence = status === 'completed' && promise.workflow_status !== 'completed'
  const needsConfirmation = status === 'completed' && (needsEvidence || Boolean(evidence.trim() || url.trim()))
  return <>
    <form className="form-panel lifecycle-editor" onSubmit={event => void save(event)}>
      <ErrorMessage error={failure} />
      <fieldset disabled={busy}>
        <div className="memory-filters">
          <label>Updated by<select aria-label="Updated by" value={actor || ''} required onChange={event => setActor(Number(event.target.value))}>
            <option value="">Choose a profile</option>
            {users.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}
          </select></label>
          <label>Commitment status<select aria-label="Commitment status" value={status} onChange={event => setStatus(event.target.value as WorkflowStatus)}>
            {workflowStatuses.map(value => <option key={value} value={value}>{statusLabel(value)}</option>)}
          </select></label>
          <label>Confirmed due date<input type="date" value={due} onChange={event => setDue(event.target.value)} /></label>
        </div>
        {promise.suggested_due_date && <div className="memory-inline">
          <p>From “{promise.deadline}” in the original meeting</p>
          <Button variant="outline" onClick={() => setDue(promise.suggested_due_date ?? '')}>Use {formatDate(promise.suggested_due_date)}</Button>
        </div>}
        {data.deadline_suggestions.length > 0 && <details className="memory-matches">
          <summary>Possible deadline changes</summary>
          {data.deadline_suggestions.map(item => <div key={item.date} className="memory-source">
            <blockquote>{item.quote}</blockquote>
            <a className="text-link" href={'#/meetings/' + item.meeting_id}>{item.meeting_title}{item.start !== null && ' · ' + item.start.toFixed(2) + ' s'}</a>
            <Button variant="outline" onClick={() => setDue(item.date)}>Use {formatDate(item.date)}</Button>
          </div>)}
        </details>}
        <div className="lifecycle-editor-section">
          <h3>Prerequisites</h3>
          {dependencies.map(item => <div className="memory-inline" key={item.id}>
            <span>{item.task} · {item.meeting_title} · {statusLabel(item.status)}</span>
            <Button variant="ghost" aria-label={'Remove ' + item.task} onClick={() => setDependencies(current => current.filter(row => row.id !== item.id))}>Remove</Button>
          </div>)}
          <div className="memory-inline memory-deadline-filter">
            <label>Find a prerequisite<input type="search" maxLength={150} value={search} onChange={event => setSearch(event.target.value)} /></label>
            <Button variant="outline" disabled={searching} onClick={() => void findDependencies()}>{searching ? 'Searching…' : 'Find'}</Button>
          </div>
          {available?.map(item => <div className="memory-inline" key={item.id}>
            <span>{item.task} · {item.meeting_title}</span>
            <Button variant="ghost" aria-label={'Add ' + item.task} onClick={() => setDependencies(current => [...current, item])}>Add</Button>
          </div>)}
          {available?.length === 0 && <p>No more matching commitments.</p>}
        </div>
        {status === 'completed' && <div className="lifecycle-editor-section">
          {blocked && <p role="status">Complete all prerequisites before closing this commitment.</p>}
          <label>Completion evidence<textarea rows={3} maxLength={2000} value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
          <label>Evidence link<input type="url" value={url} onChange={event => setUrl(event.target.value)} /></label>
          {needsConfirmation && <label className="check-label">
            <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> I confirm this commitment is complete
          </label>}
        </div>}
        <div className="memory-inline">
          <Button type="submit" disabled={blocked || (needsEvidence && !evidence.trim() && !url.trim()) || (needsConfirmation && !confirmed)}>Save lifecycle update</Button>
          {(failure || blocked) && <Button variant="ghost" onClick={onReload}>Reload latest</Button>}
        </div>
      </fieldset>
    </form>
    {data.history.length > 0 && <details className="memory-matches">
      <summary>Activity history</summary>
      {data.history.map(event => <div key={event.id}>
        <h3>{event.actor} <small>{new Date(event.recorded_at).toLocaleString()}</small></h3>
        {event.changes.status && <p>{statusLabel(String(event.changes.status.from))} → {statusLabel(String(event.changes.status.to))}</p>}
        {event.changes.due_date && <p>Due date changed from {displayDate(event.changes.due_date.from)} to {displayDate(event.changes.due_date.to)}</p>}
        {event.changes.prerequisites && <p>Prerequisites updated</p>}
        {event.evidence && <p className="lifecycle-evidence">{event.evidence}</p>}
        {event.evidence_url && <a className="text-link" href={event.evidence_url} target="_blank" rel="noreferrer">Open evidence link</a>}
      </div>)}
    </details>}
  </>
}

function displayDate(value: unknown) {
  return typeof value === 'string' ? formatDate(value) : 'no date'
}

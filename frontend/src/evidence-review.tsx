import { useState, type FormEvent } from 'react'
import { api, type EvidenceReview, type User, type Verdict } from './api'
import { Button, ErrorMessage } from './components'

const verdicts: Record<Verdict, string> = {
  supports: 'Supports completion', insufficient: 'Needs more evidence', contradicts: 'Conflicts with completion',
}

export function EvidenceReviewPanel({ id, users }: { id: number; users: User[] }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<EvidenceReview>()
  const [reviewer, setReviewer] = useState(users.length === 1 ? users[0].id : 0)
  const [verdict, setVerdict] = useState<Verdict | ''>('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  async function load() {
    setBusy(true)
    setSaved(false)
    setError('')
    try { setData(await api.evidenceReview(id)) }
    catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!data?.evidence || !reviewer || !verdict) return
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      setData(await api.saveEvidenceReview(id, {
        event_id: data.evidence.id, revision: data.reviews[0]?.id ?? 0, reviewer_id: reviewer, verdict, note: note.trim() || null,
      }))
      setNote('')
      setVerdict('')
      setSaved(true)
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  return <div className="lifecycle-panel">
    <Button variant="outline" aria-expanded={open} onClick={() => {
      setOpen(!open)
      if (!open) void load()
    }}>Review completion evidence</Button>
    {open && <div className="form-panel lifecycle-editor evidence-review">
      <ErrorMessage error={error} />
      {error && <Button variant="outline" disabled={busy} onClick={() => void load()}>Reload evidence</Button>}
      {busy && <p role="status">Checking evidence record…</p>}
      {saved && <p className="success" role="status">Evidence review saved.</p>}
      {data && !data.evidence && <p>No current completion evidence is available.</p>}
      {data?.evidence && <>
        <h3>Submitted evidence</h3>
        <p>{data.evidence.submitted_by} · {new Date(data.evidence.submitted_at).toLocaleString()}</p>
        {data.evidence.note && <p className="lifecycle-evidence">{data.evidence.note}</p>}
        {data.evidence.url && <a className="text-link" href={data.evidence.url} target="_blank" rel="noreferrer">Open evidence link</a>}
        <p>Latest review · {data.reviews[0] ? verdicts[data.reviews[0].verdict] : 'Awaiting review'}</p>
        <form onSubmit={event => void save(event)}>
          <fieldset disabled={busy}>
            <div className="memory-filters">
              <label>Reviewer<select aria-label="Reviewer" required value={reviewer || ''} onChange={event => setReviewer(Number(event.target.value))}>
                <option value="">Choose a profile</option>
                {users.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}
              </select></label>
              <label>Evidence verdict<select aria-label="Evidence verdict" required value={verdict} onChange={event => setVerdict(event.target.value as Verdict | '')}>
                <option value="">Choose a verdict</option>
                {Object.entries(verdicts).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
              </select></label>
            </div>
            <label>Review notes<textarea rows={3} maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></label>
            <Button type="submit">Save evidence review</Button>
          </fieldset>
        </form>
        {data.reviews.length > 0 && <details className="memory-matches">
          <summary>Review history</summary>
          {data.reviews.map(review => <div key={review.id}>
            <h3>{verdicts[review.verdict]}</h3>
            <p>{review.reviewer} · {new Date(review.reviewed_at).toLocaleString()}</p>
            {review.note && <p className="lifecycle-evidence">{review.note}</p>}
          </div>)}
        </details>}
      </>}
    </div>}
  </div>
}

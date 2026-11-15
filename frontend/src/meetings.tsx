import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowUpRight, CalendarDays, FileText, Plus, Search, Trash2 } from 'lucide-react'
import { api, type Meeting, type MeetingInput, type Transcript, type User } from './api'
import { Button, EmptyState, ErrorMessage, formatDate, today } from './components'
import { AudioEditor } from './audio'
import { CommitmentReview } from './commitments'

export function MeetingList({ meetings, users, filtered = false }: { meetings: Meeting[]; users: User[]; filtered?: boolean }) {
  const [query, setQuery] = useState('')
  const ownerName = (meeting: Meeting) => users.find(user => user.id === meeting.owner_id)?.name ?? 'Unknown'
  const term = query.trim().toLowerCase()
  const results = meetings.filter(meeting => (meeting.title + ' ' + ownerName(meeting)).toLowerCase().includes(term))
  return <section className="panel" aria-labelledby="meeting-list-title">
    <div className="panel-heading">
      <div><h2 id="meeting-list-title">{filtered ? 'All meetings' : 'Recent meetings'}</h2><p>Open a meeting to read or edit its transcript.</p></div>
      {filtered ? <label className="search"><Search size={17} /><input aria-label="Search meetings" placeholder="Search meetings" value={query} onChange={event => setQuery(event.target.value)} /></label>
        : <a className="text-link" href="#/meetings">View all <ArrowUpRight size={16} /></a>}
    </div>
    {results.length > 0 ? <ul className="meeting-list">
      {results.map(meeting => <li key={meeting.id}>
        <a href={'#/meetings/' + meeting.id}><span className="row-icon"><CalendarDays size={19} /></span>
          <span className="row-main"><strong>{meeting.title}</strong><small>{formatDate(meeting.date)} · {ownerName(meeting)}</small></span>
          <span className={meeting.has_transcript ? 'badge badge-positive' : 'badge'}>{meeting.has_transcript ? 'Transcript saved' : 'No transcript'}</span>
          <ArrowUpRight size={16} className="row-arrow" />
        </a>
      </li>)}
    </ul> : <EmptyState icon={<CalendarDays size={25} />} title={term ? 'No matching meetings' : 'No meetings yet'}>
      <p>{term ? 'Try another title or profile name.' : 'Create your first meeting to start capturing conversations.'}</p>
      {!term && <a className="button button-outline" href="#/meetings/new"><Plus size={17} /> New meeting</a>}
    </EmptyState>}
  </section>
}

export function MeetingEditor({ id, users, onChange }: { id?: number; users: User[]; onChange: () => void }) {
  const [meeting, setMeeting] = useState<MeetingInput>({ title: '', date: today(), owner_id: users[0]?.id ?? 0 })
  const [transcript, setTranscript] = useState<Transcript>({ text: '', updated_at: null })
  const [draftText, setDraftText] = useState('')
  const [loading, setLoading] = useState(Boolean(id))
  const [loadFailed, setLoadFailed] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  useEffect(() => {
    if (id === undefined) return
    let current = true
    Promise.all([api.meeting(id), api.transcript(id)]).then(([entry, saved]) => {
      if (!current) return
      setMeeting({ title: entry.title, date: entry.date, owner_id: entry.owner_id })
      setTranscript(saved)
      setDraftText(saved.text)
    }).catch((failure: Error) => {
      if (current) {
        setLoadFailed(true)
        setError(failure.message)
      }
    }).finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [id])

  async function submit(event: FormEvent, action: () => Promise<void>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
      onChange()
    } catch (failure) {
      setError((failure as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return <>
    <a className="back-link" href="#/meetings"><ArrowLeft size={16} /> Meetings</a>
    <header className="page-heading"><div><h1>{id ? 'Meeting details' : 'New meeting'}</h1>
      <p>{id ? 'Manage the meeting and its saved transcript.' : 'Add the details, then enter the transcript.'}</p></div></header>
    <ErrorMessage error={error} />
    {notice && <div className="success" role="status">{notice}</div>}
    {loading && <p role="status">Loading meeting…</p>}
    {!loading && !loadFailed && (users.length === 0 ?
      <section className="panel"><EmptyState icon={<CalendarDays />} title="Create a profile first"><p>A meeting needs a profile as its owner.</p><a className="button button-primary" href="#/settings">Open settings</a></EmptyState></section>
      : <div className="editor-grid">
        <form className="panel form-panel" onSubmit={event => void submit(event, async () => {
          const saved = await api.saveMeeting(meeting, id)
          if (!id) window.location.hash = '/meetings/' + saved.id
          else setNotice('Meeting details saved.')
        })}>
          <h2>Meeting details</h2>
          <fieldset disabled={busy}>
            <label>Title<input required maxLength={200} value={meeting.title} onChange={event => setMeeting({ ...meeting, title: event.target.value })} placeholder="e.g. Product planning" /></label>
            <label>Date<input type="date" required value={meeting.date} onChange={event => setMeeting({ ...meeting, date: event.target.value })} /></label>
            <label>Owner<select required value={meeting.owner_id} onChange={event => setMeeting({ ...meeting, owner_id: Number(event.target.value) })}>
              <option value={0} disabled>Select a profile</option>
              {users.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}
            </select></label>
            <Button type="submit">{id ? 'Save details' : 'Create meeting'}</Button>
          </fieldset>
        </form>
        {id !== undefined && <form className="panel form-panel" onSubmit={event => void submit(event, async () => {
          const saved = await api.saveTranscript(id, draftText)
          setTranscript(saved)
          setNotice('Transcript saved.')
        })}>
          <h2><FileText size={20} /> Transcript</h2>
          <fieldset disabled={busy}>
            <label>Manual transcript<textarea rows={11} maxLength={200000} value={draftText} onChange={event => setDraftText(event.target.value)} placeholder="Paste a transcript or type your meeting notes…" /></label>
            {transcript.updated_at && <small>Last saved {new Date(transcript.updated_at).toLocaleString()}</small>}
            <Button type="submit" disabled={draftText === transcript.text && Boolean(transcript.updated_at)}>Save transcript</Button>
          </fieldset>
        </form>}
      </div>)}
    {id !== undefined && !loading && !loadFailed && <AudioEditor id={id} onChange={onChange} />}
    {id !== undefined && !loading && !loadFailed && <CommitmentReview id={id} />}
    {id !== undefined && !loading && !loadFailed && <div className="delete-meeting">
      {confirmDelete ? <form onSubmit={event => void submit(event, async () => {
        await api.deleteMeeting(id)
        window.location.hash = '/meetings'
      })}>
        <p>Delete this meeting and its transcripts?</p>
        <Button variant="outline" onClick={() => setConfirmDelete(false)} disabled={busy}>Cancel</Button>
        <Button variant="destructive" type="submit" disabled={busy}>Delete meeting</Button>
      </form> : <Button variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 size={16} /> Delete meeting</Button>}
    </div>}
  </>
}

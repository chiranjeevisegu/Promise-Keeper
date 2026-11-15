import { useEffect, useState } from 'react'
import { api, type Analysis, type Commitment, type CommitmentDetails } from './api'
import { Button, ErrorMessage } from './components'

const fields = ['owner', 'task', 'recipient', 'deadline'] as const

export function CommitmentReview({ id }: { id: number }) {
  const [source, setSource] = useState<'manual' | 'audio'>('manual')
  return <section className="panel form-panel audio-panel">
    <h2>Commitment review</h2>
    <label>Transcript source<select value={source} onChange={event => setSource(event.target.value as typeof source)}>
      <option value="manual">Manual transcript</option>
      <option value="audio">Audio transcript</option>
    </select></label>
    <Review key={source} id={id} source={source} />
  </section>
}

function Review({ id, source }: { id: number; source: 'manual' | 'audio' }) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [drafts, setDrafts] = useState<Record<string, CommitmentDetails>>({})
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [manual, setManual] = useState<Commitment[]>([])
  const [extractDetails, setExtractDetails] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)

  function load(value: Analysis | null) {
    setAnalysis(value)
    setDrafts(Object.fromEntries(value?.candidates.map(candidate => [candidate.id, candidate.details]) ?? []))
    setSelected({})
    setManual([])
  }

  useEffect(() => {
    let current = true
    api.commitments(id, source).then(value => { if (current) load(value) })
      .catch((failure: Error) => { if (current) setError(failure.message) })
      .finally(() => { if (current) setBusy(false) })
    return () => { current = false }
  }, [id, source])

  async function run(action: () => Promise<Analysis | null>) {
    setBusy(true)
    setError('')
    try { load(await action()) }
    catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  const approved = Object.fromEntries(Object.entries(drafts).filter(([key]) => selected[key]))
  const invalid = Object.values(approved).some(details => !details.task?.trim())
  const remaining = analysis?.unflagged?.filter(item => !manual.some(candidate => candidate.id === item.id)) ?? []
  const linePickerLabel = analysis?.manual_review ? 'Add a commitment' : 'Add a missed commitment'

  return <div className="commitment-review">
    <ErrorMessage error={error} />
    {busy && <p role="status">Working on commitment analysis…</p>}
    {(!analysis || analysis.status === 'processing') && <label className="check-label"><input type="checkbox" checked={extractDetails} disabled={busy}
      onChange={event => setExtractDetails(event.target.checked)} /> Automatically fill commitment details</label>}
    <Button disabled={busy} onClick={() => void run(() => api.analyzeCommitments(id, source, extractDetails))}>
      {analysis ? 'Check saved transcript' : 'Analyze saved transcript'}
    </Button>
    {(!analysis || analysis.status === 'processing') && <Button disabled={busy}
      onClick={() => void run(() => api.analyzeCommitments(id, source, false, true))}>Review transcript manually</Button>}
    {analysis?.status === 'review' && <>
      {analysis.manual_review
        ? <p>Choose transcript lines and enter the commitments you want to confirm.</p>
        : <p>Select the commitments to confirm. Unselected candidates will be dismissed.</p>}
      {!analysis.manual_review && analysis.candidates.length === 0 && <p>The model found no candidates. You can still add a commitment from the transcript.</p>}
      {analysis.extraction_unavailable && <p>Local extraction was unavailable. Enter any missing details manually.</p>}
      <fieldset disabled={busy}>
        {remaining.length > 0 && <label>{linePickerLabel}<select aria-label={linePickerLabel} value="" onChange={event => {
          if (!event.target.value) return
          const line = remaining.find(item => item.id === Number(event.target.value))
          if (!line) return
          const details = { owner: line.speaker, task: null, recipient: null, deadline: null }
          setManual([...manual, { ...line, details, label: 'manual', scores: {} }])
          setDrafts({ ...drafts, [line.id]: details })
          setSelected({ ...selected, [line.id]: true })
        }}>
          <option value="">Select a transcript line</option>
          {remaining.map(line => <option key={line.id} value={line.id}>{line.speaker ? line.speaker + ': ' : ''}{line.quote}</option>)}
        </select></label>}
        {[...analysis.candidates, ...manual].map(candidate => <article key={candidate.id} className="commitment-card">
          <Evidence candidate={candidate} />
          <label className="check-label"><input type="checkbox" checked={Boolean(selected[candidate.id])} onChange={event =>
            setSelected({ ...selected, [candidate.id]: event.target.checked })} /> Confirm this commitment</label>
          {selected[candidate.id] && fields.map(field => <label className="commitment-field" key={field}>{field}
            <input maxLength={field === 'task' ? 1000 : 150} value={drafts[candidate.id]?.[field] ?? ''} onChange={event =>
              setDrafts({ ...drafts, [candidate.id]: { ...drafts[candidate.id], [field]: event.target.value || null } })} />
          </label>)}
        </article>)}
        <Button disabled={invalid} onClick={() => void run(() => api.confirmCommitments(id, source, analysis.revision, approved))}>Finish review</Button>
      </fieldset>
    </>}
    {analysis?.status === 'complete' && <div>
      <h3>{analysis.confirmed.length ? 'Confirmed commitments' : 'No confirmed commitments'}</h3>
      {analysis.confirmed.length > 0 && <a className="text-link" href="#/ledger">View Promise Ledger</a>}
      {analysis.confirmed.map(candidate => <article key={candidate.id} className="commitment-card">
        <Evidence candidate={candidate} />
        <p>{[candidate.details.task, candidate.details.owner, candidate.details.recipient, candidate.details.deadline].filter(Boolean).join(' · ')}</p>
      </article>)}
    </div>}
  </div>
}

function Evidence({ candidate }: { candidate: Commitment }) {
  return <>
    <blockquote>{candidate.quote}</blockquote>
    <small>{candidate.speaker ?? 'Unknown speaker'}{candidate.start !== null && ' · ' + candidate.start.toFixed(2) + ' s'}
      {candidate.label === 'manual' ? ' · Added during review' : ' · ' + candidate.label.replaceAll('_', ' ') + ' · ' + Math.round((candidate.scores[candidate.label] ?? 0) * 100) + '%'}</small>
  </>
}

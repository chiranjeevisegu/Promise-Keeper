import { useState } from 'react'
import { api } from './api'
import { Button, ErrorMessage } from './components'

export function FollowUpDraft({ id }: { id: number }) {
  const [draft, setDraft] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function toggle() {
    setError('')
    setNotice('')
    if (draft !== null) {
      setDraft(null)
      return
    }
    setBusy(true)
    try { setDraft((await api.followUp(id)).draft) }
    catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(draft ?? '')
      setNotice('Draft copied.')
    } catch {
      setNotice('Select and copy the draft text.')
    }
  }

  return <div className="lifecycle-panel">
    <Button variant="outline" aria-expanded={draft !== null} disabled={busy} onClick={() => void toggle()}>
      {draft !== null ? 'Close follow-up draft' : 'Draft follow-up'}
    </Button>
    <ErrorMessage error={error} />
    {draft !== null && <div className="form-panel follow-up-editor">
      <label>Follow-up draft<textarea value={draft} rows={4} onChange={event => { setDraft(event.target.value); setNotice('') }} /></label>
      <div className="memory-inline">
        <Button variant="outline" disabled={!draft.trim()} onClick={() => void copy()}>Copy draft</Button>
        {notice && <p role="status">{notice}</p>}
      </div>
    </div>}
  </div>
}

import { useEffect, useState, type FormEvent } from 'react'
import { AudioLines, Upload } from 'lucide-react'
import { api, type AudioTranscript } from './api'
import { Button, ErrorMessage } from './components'

function timestamp(seconds: number) {
  const milliseconds = Math.round(seconds * 1000)
  const minutes = Math.floor(milliseconds / 60000)
  const remainder = String(Math.floor(milliseconds / 1000) % 60).padStart(2, '0')
  return minutes + ':' + remainder + '.' + String(milliseconds % 1000).padStart(3, '0')
}

export function AudioEditor({ id, onChange }: { id: number; onChange: () => void }) {
  const [entry, setEntry] = useState<AudioTranscript | null>()
  const [file, setFile] = useState<File>()
  const [available, setAvailable] = useState(false)
  const [diarize, setDiarize] = useState(false)
  const [language, setLanguage] = useState<'auto' | 'en'>('auto')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editSpeakers, setEditSpeakers] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let current = true
    Promise.all([api.audioTranscript(id), api.audioCapabilities()]).then(([saved, capabilities]) => {
      if (current) {
        setEntry(saved)
        setAvailable(capabilities.diarization)
      }
    }).catch((failure: Error) => { if (current) setError(failure.message) })
    return () => { current = false }
  }, [id, attempt])

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await action()
      onChange()
    } catch (failure) {
      setError((failure as Error).message)
    } finally {
      setBusy('')
    }
  }

  function upload(event: FormEvent) {
    event.preventDefault()
    if (!file) return
    if (!/\.(mp3|m4a|mp4|wav)$/i.test(file.name) || !file.size || file.size > 100 * 1024 * 1024) {
      setError('Choose a non-empty MP3, WAV, M4A, or MP4 recording up to 100 MB.')
      return
    }
    void run('Transcribing recording…', async () => {
      setEntry(await api.transcribe(id, file, diarize, language))
      setFile(undefined)
      setNotice('Audio transcript saved.')
    })
  }

  const speakers = [...new Set(entry?.segments.flatMap(segment => segment.speaker?.trim() ? [segment.speaker.trim()] : []) ?? [])]
  const assignedName = (speaker: string) => entry && Object.hasOwn(entry.names, speaker) ? entry.names[speaker] : ''

  return <section className="panel form-panel audio-panel">
    <h2><AudioLines size={21} /> Audio transcription</h2>
    <ErrorMessage error={error} />
    {busy && <p className="success" role="status">{busy}</p>}
    {notice && <p className="success" role="status">{notice}</p>}
    {entry === undefined && (error
      ? <Button variant="outline" onClick={() => { setError(''); setAttempt(attempt + 1) }}>Retry</Button>
      : <p role="status">Loading audio transcript…</p>)}
    {entry === null && <form onSubmit={upload}>
      <fieldset disabled={Boolean(busy)}>
        <label>Meeting recording<input type="file" required accept=".mp3,.wav,.m4a,.mp4" onChange={event => setFile(event.target.files?.[0])} /></label>
        <label>Speech language<select aria-label="Speech language" value={language}
          onChange={event => setLanguage(event.target.value as typeof language)}>
          <option value="auto">Auto detect</option>
          <option value="en">English</option>
        </select></label>
        <small>MP3, WAV, M4A, or MP4 · up to 100 MB and 60 minutes. The first run downloads the speech model.</small>
        {available && <label className="check-label"><input type="checkbox" checked={diarize} onChange={event => setDiarize(event.target.checked)} /> Detect speakers</label>}
        <Button type="submit" disabled={!file}><Upload size={17} /> Transcribe recording</Button>
      </fieldset>
    </form>}
    {entry && <form onSubmit={event => {
      event.preventDefault()
      void run('Saving changes…', async () => {
        setEntry(await api.saveAudioTranscript(id, entry))
        setNotice('Audio transcript changes saved.')
      })
    }}>
      <fieldset disabled={Boolean(busy)}>
        <label className="check-label"><input type="checkbox" checked={editSpeakers} onChange={event => setEditSpeakers(event.target.checked)} /> Edit speakers per segment</label>
        {speakers.length > 0 && <div className="speaker-names">{speakers.map(speaker =>
          <label key={speaker}>{speaker}<input placeholder="Participant name" maxLength={100} value={assignedName(speaker)} onChange={event => setEntry({ ...entry, names: { ...entry.names, [speaker]: event.target.value } })} /></label>)}</div>}
        <ol className="audio-segments">{entry.segments.map((segment, index) => <li key={index}>
          <label><span>{timestamp(segment.start)} – {timestamp(segment.end)}{segment.speaker && ' · ' + (assignedName(segment.speaker) || segment.speaker)}</span>
            <textarea rows={2} maxLength={4000} value={segment.text} onChange={event => {
              const segments = [...entry.segments]
              segments[index] = { ...segment, text: event.target.value }
              setEntry({ ...entry, segments })
            }} />
          </label>
          {editSpeakers && <label className="audio-speaker-edit">Speaker for segment {index + 1}
            <input maxLength={100} value={segment.speaker ?? ''} onChange={event => {
              const segments = [...entry.segments]
              segments[index] = { ...segment, speaker: event.target.value || null }
              setEntry({ ...entry, segments })
            }} />
          </label>}
        </li>)}</ol>
        <div className="audio-actions">
          <Button type="submit">Save audio transcript</Button>
          {confirmDelete ? <>
            <Button variant="destructive" onClick={() => void run('Deleting transcript…', async () => {
              await api.deleteAudioTranscript(id)
              setEntry(null)
              setConfirmDelete(false)
              setNotice('Audio transcript deleted.')
            })}>Confirm delete</Button>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>Cancel</Button>
          </> : <Button variant="ghost" onClick={() => setConfirmDelete(true)}>Delete audio transcript</Button>}
        </div>
      </fieldset>
    </form>}
  </section>
}

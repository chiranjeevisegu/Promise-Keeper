import { useState, type FormEvent } from 'react'
import { Plus, UserRound } from 'lucide-react'
import { api, type User, type UserInput } from './api'
import { Button, ErrorMessage } from './components'

export function Settings({ users, onChange }: { users: User[]; onChange: () => void }) {
  const [selected, setSelected] = useState<User | undefined>()
  const [profile, setProfile] = useState<UserInput>({ name: '', email: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  function select(user?: User) {
    setSelected(user)
    setProfile(user ? { name: user.name, email: user.email } : { name: '', email: '' })
    setError('')
    setNotice('')
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      select(await api.saveUser(profile, selected?.id))
      setNotice('Profile saved.')
      onChange()
    } catch (failure) {
      setError((failure as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return <>
    <header className="page-heading"><div><h1>Settings</h1><p>Manage local profiles for meeting ownership.</p></div></header>
    <div className="editor-grid">
      <section className="panel form-panel">
        <h2>Profiles</h2>
        <div className="profile-list">{users.map(user =>
          <button key={user.id} className="profile-item" type="button" onClick={() => select(user)} disabled={busy}>
            <span className="row-icon"><UserRound size={17} /></span><span><strong>{user.name}</strong><small>{user.email}</small></span>
          </button>)}</div>
        {users.length === 0 && <p>No profiles yet.</p>}
        {selected && <Button variant="outline" onClick={() => select()} disabled={busy}><Plus size={17} /> New profile</Button>}
      </section>
      <form className="panel form-panel" onSubmit={save}>
        <h2>{selected ? 'Edit profile' : 'New profile'}</h2>
        <ErrorMessage error={error} />
        {notice && <div className="success" role="status">{notice}</div>}
        <fieldset disabled={busy}>
          <label>Name<input required maxLength={100} autoComplete="name" value={profile.name} onChange={event => setProfile({ ...profile, name: event.target.value })} /></label>
          <label>Email<input required type="email" maxLength={254} autoComplete="email" value={profile.email} onChange={event => setProfile({ ...profile, email: event.target.value })} /></label>
          <Button type="submit">Save profile</Button>
        </fieldset>
      </form>
    </div>
  </>
}

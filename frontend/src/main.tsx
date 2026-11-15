import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { Bell, BookCheck, CalendarDays, ChartNoAxesCombined, ChevronRight, Compass, FileText, LayoutDashboard, Moon, Plus, Settings as SettingsIcon, ShieldCheck, Sun, UsersRound } from 'lucide-react'
import { api, type Meeting, type User } from './api'
import { Button, ErrorMessage } from './components'
import { MeetingEditor, MeetingList } from './meetings'
import { Insights } from './insights'
import { MemoryExplorer, PromiseLedger } from './memory'
import { Notifications } from './notifications'
import { Settings } from './settings'
import './style.css'

const navigation = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'meetings', label: 'Meetings', icon: CalendarDays },
  { id: 'ledger', label: 'Promise Ledger', icon: BookCheck },
  { id: 'memory', label: 'Memory Explorer', icon: Compass },
  { id: 'insights', label: 'Insights', icon: ChartNoAxesCombined },
  { id: 'notifications', label: 'Notifications', icon: Bell },
  { id: 'settings', label: 'Settings', icon: SettingsIcon },
]
const readRoute = () => window.location.hash.slice(2) || 'overview'

function App() {
  const [route, setRoute] = useState(readRoute)
  const [dark, setDark] = useState(() => localStorage.getItem('promise-keeper-theme') === 'dark')
  const [data, setData] = useState<{ users: User[]; meetings: Meeting[] }>()
  const [failure, setFailure] = useState('')
  const [access, setAccess] = useState<{ required: boolean; authenticated: boolean }>()
  const [authError, setAuthError] = useState('')
  const active = navigation.find(item => item.id === route.split('/')[0])
  const match = route.match(/^meetings\/(\d+)$/)
  const promise = route.match(/^ledger\/(\d+)$/)
  const graph = route.match(/^insights\/(\d+)$/)

  useEffect(() => {
    const updateRoute = () => { setRoute(readRoute()); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', updateRoute)
    return () => window.removeEventListener('hashchange', updateRoute)
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('promise-keeper-theme', dark ? 'dark' : 'light')
  }, [dark])

  const refresh = useCallback(async () => {
    setFailure('')
    try {
      const [users, meetings] = await Promise.all([api.users(), api.meetings()])
      setData({ users, meetings })
    } catch (error) {
      setFailure((error as Error).message)
    }
  }, [])

  const checkAccess = useCallback(async () => {
    setAuthError('')
    try { setAccess(await api.authStatus()) }
    catch (error) { setAuthError((error as Error).message) }
  }, [])

  useEffect(() => {
    void checkAccess()
    function expired() {
      setData(undefined)
      setAccess({ required: true, authenticated: false })
    }
    window.addEventListener('pk-session-expired', expired)
    return () => window.removeEventListener('pk-session-expired', expired)
  }, [checkAccess])

  useEffect(() => {
    if (access?.authenticated) void refresh()
  }, [access?.authenticated, refresh])

  async function signOut() {
    try {
      await api.signOut()
      setData(undefined)
      setFailure('')
      setAccess({ required: true, authenticated: false })
    } catch (error) { setFailure((error as Error).message) }
  }

  if (!access) return <main className="access-screen">
    <h1>Promise Keeper</h1>
    <ErrorMessage error={authError} />
    {authError ? <Button onClick={() => void checkAccess()}>Retry connection</Button> : <p role="status">Checking workspace access…</p>}
  </main>
  if (!access.authenticated) return <SignIn onSuccess={() => {
    setFailure('')
    setAccess({ required: true, authenticated: true })
  }} />

  return <div className="workspace">
    <aside className="sidebar">
      <a href="#/overview" className="brand"><span><ShieldCheck size={23} /></span><strong>Promise Keeper</strong></a>
      <nav aria-label="Main navigation">
        {navigation.map(({ id, label, icon: Icon }) => <a key={id} href={'#/' + id} className={active?.id === id ? 'nav-link selected' : 'nav-link'} aria-current={active?.id === id ? 'page' : undefined}>
          <Icon size={19} />{label}
        </a>)}
      </nav>
      <div className="sidebar-footer"><span /> Local workspace</div>
    </aside>
    <div className="main-column">
      <div className="topbar">
        <div className="breadcrumb">Workspace <ChevronRight size={15} /> <strong>{active?.label ?? 'Page not found'}</strong></div>
        <div className="memory-inline">
          <Button variant="ghost" className="theme-toggle" aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'} title={dark ? 'Switch to light theme' : 'Switch to dark theme'} onClick={() => setDark(!dark)}>
            {dark ? <Sun size={19} /> : <Moon size={19} />}
          </Button>
          {access.required && <Button variant="outline" onClick={() => void signOut()}>Sign out</Button>}
        </div>
      </div>
      <main>
        {failure && <><ErrorMessage error={failure} /><Button variant="outline" onClick={() => void refresh()}>Retry connection</Button></>}
        {!data && !failure && <p role="status">Loading workspace…</p>}
        {data && <>
          {route === 'overview' && <>
            <header className="page-heading"><div><h1>Workspace overview</h1><p>Capture meetings and keep their transcripts in one place.</p></div><a className="button button-primary" href="#/meetings/new"><Plus size={18} /> New meeting</a></header>
            <div className="stats">
              <Stat icon={<CalendarDays size={20} />} title="Meetings" count={data.meetings.length} color="violet" />
              <Stat icon={<FileText size={20} />} title="Saved transcripts" count={data.meetings.filter(meeting => meeting.has_transcript).length} color="green" />
              <Stat icon={<UsersRound size={20} />} title="Profiles" count={data.users.length} color="blue" />
            </div>
            {data.users.length === 0 && <section className="panel onboarding"><div><h2>Set up your profile</h2><p>Create a profile to assign an owner to your meetings.</p></div><a className="button button-outline" href="#/settings">Create profile <ChevronRight size={16} /></a></section>}
            <MeetingList meetings={data.meetings.slice(0, 5)} users={data.users} />
          </>}
          {route === 'meetings' && <>
            <header className="page-heading"><div><h1>Meetings</h1><p>Manage meetings and their transcripts.</p></div><a className="button button-primary" href="#/meetings/new"><Plus size={18} /> New meeting</a></header>
            <MeetingList meetings={data.meetings} users={data.users} filtered />
          </>}
          {(route === 'meetings/new' || match) && <MeetingEditor key={route} id={match ? Number(match[1]) : undefined} users={data.users} onChange={() => void refresh()} />}
          {(route === 'ledger' || promise) && <PromiseLedger key={route} meetings={data.meetings} users={data.users} selectedId={promise ? Number(promise[1]) : undefined} />}
          {route === 'memory' && <MemoryExplorer meetings={data.meetings} users={data.users} />}
          {(route === 'insights' || graph) && <Insights key={route} focus={graph ? Number(graph[1]) : undefined} />}
          {route === 'notifications' && <Notifications users={data.users} />}
          {route === 'settings' && <Settings users={data.users} onChange={() => void refresh()} />}
          {(!active || (active.id !== route && route !== 'meetings/new' && !match && !promise && !graph)) && <><h1>Page not found</h1><a className="text-link" href="#/overview">Go to overview</a></>}
        </>}
      </main>
    </div>
  </div>
}

function SignIn({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await api.signIn(password)
      setPassword('')
      onSuccess()
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }
  return <main className="access-screen">
    <section className="panel form-panel access-card">
      <h1>Sign in to Promise Keeper</h1>
      <ErrorMessage error={error} />
      <form onSubmit={event => void submit(event)}>
        <fieldset disabled={busy}>
          <label>Workspace password<input type="password" autoComplete="current-password" required maxLength={1024}
            value={password} onChange={event => setPassword(event.target.value)} /></label>
          <Button type="submit">{busy ? 'Signing in…' : 'Sign in'}</Button>
        </fieldset>
      </form>
    </section>
  </main>
}

function Stat({ icon, title, count, color }: { icon: React.ReactNode; title: string; count: number; color: string }) {
  return <div className="panel stat"><span className={'stat-icon ' + color}>{icon}</span><div><p>{title}</p><strong>{count}</strong></div></div>
}

createRoot(document.getElementById('root')!).render(<App />)

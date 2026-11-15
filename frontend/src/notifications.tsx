import { useEffect, useState, type FormEvent } from 'react'
import { Bell, ChevronRight, Clock3 } from 'lucide-react'
import { api, type Inbox, type NotificationSettings, type User } from './api'
import { Button, EmptyState, ErrorMessage } from './components'
import { FollowUpDraft } from './follow-up'

const flags = [
  ['upcoming', 'Upcoming deadlines'], ['overdue', 'Overdue alerts'], ['daily_summary', 'Daily accountability summary'],
] as const
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

function timeLabel(value: string, timezone: string) {
  const date = new Date(value)
  const format = { dateStyle: 'medium', timeStyle: 'short' } as const
  try { return date.toLocaleString(undefined, { ...format, timeZone: timezone }) + ' · ' + timezone }
  catch { return date.toLocaleString(undefined, { ...format, timeZone: 'UTC' }) + ' UTC' }
}

export function Notifications({ users }: { users: User[] }) {
  const [selected, setSelected] = useState(users[0]?.id)
  const profile = users.find(user => user.id === selected) ?? users[0]

  return <>
    <header className="page-heading"><div><h1>Notifications</h1><p>Review reminders, overdue commitments, and daily summaries.</p></div></header>
    {users.length > 1 && <div className="panel form-panel notification-profile">
      <label>Notification profile<select aria-label="Notification profile" value={profile?.id ?? ''} onChange={event => setSelected(Number(event.target.value))}>
        {users.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}
      </select></label>
    </div>}
    {profile ? <NotificationCenter key={profile.id} profile={profile} /> :
      <section className="panel"><EmptyState icon={<Bell size={25} />} title="Create a profile to receive reminders">
        <a className="button button-outline" href="#/settings">Open settings</a>
      </EmptyState></section>}
  </>
}

function NotificationCenter({ profile }: { profile: User }) {
  const [settings, setSettings] = useState<NotificationSettings>()
  const [applied, setApplied] = useState<NotificationSettings>()
  const [inbox, setInbox] = useState<Inbox>()
  const [offset, setOffset] = useState(0)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [inboxError, setInboxError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let current = true
    api.notificationSettings(profile.id).then(({ settings, configured }) => {
      if (current) {
        setSettings(configured ? settings : { ...settings, timezone })
        setApplied(settings)
      }
    }).catch((failure: Error) => { if (current) setError(failure.message) })
    return () => { current = false }
  }, [profile.id])

  useEffect(() => {
    let current = true
    async function refresh() {
      try {
        const result = await api.inbox(profile.id, offset)
        if (current) { setInbox(result); setInboxError('') }
      } catch (failure) { if (current) setInboxError((failure as Error).message) }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => { current = false; window.clearInterval(timer) }
  }, [profile.id, offset, revision])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
      setRevision(value => value + 1)
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!settings) return
    void run(async () => {
      const stored = await api.saveNotificationSettings(profile.id, settings)
      setSettings(stored.settings)
      setApplied(stored.settings)
      setNotice('Notification preferences saved.')
    })
  }

  return <div className="memory-stack">
    <ErrorMessage error={error} />
    {notice && <p className="success" role="status">{notice}</p>}
    <section className="panel form-panel">
      <h2>Preferences for {profile.name}</h2>
      <p className="notification-help">Deadline alerts use confirmed dates for commitments assigned to this profile name.</p>
      {settings && <form onSubmit={save}>
        <fieldset disabled={busy}>
          <label className="check-label"><input type="checkbox" checked={settings.enabled} onChange={event => setSettings({ ...settings, enabled: event.target.checked })} /> Enable in-app notifications</label>
          <div className="memory-inline">
            {flags.map(([key, label]) => <label key={key} className="check-label notification-option">
              <input type="checkbox" checked={settings[key]} onChange={event => setSettings({ ...settings, [key]: event.target.checked })} /> {label}
            </label>)}
          </div>
          <div className="memory-filters">
            <label>Reminder lead time (days)<input type="number" required min={0} max={30} value={Number.isFinite(settings.lead_days) ? settings.lead_days : ''}
              onChange={event => setSettings({ ...settings, lead_days: event.target.valueAsNumber })} /></label>
            <label>Daily summary time<input type="time" required value={settings.summary_time}
              onChange={event => setSettings({ ...settings, summary_time: event.target.value })} /></label>
            <label>Timezone<input required maxLength={80} placeholder="Asia/Kolkata" value={settings.timezone}
              onChange={event => setSettings({ ...settings, timezone: event.target.value })} /></label>
          </div>
          <div className="memory-inline">
            <Button type="submit">Save preferences</Button>
            <Button variant="outline" disabled={!applied?.enabled} onClick={() => void run(async () => {
              const { created } = await api.checkNotifications(profile.id)
              setOffset(0)
              setNotice(created ? `${created} new ${created === 1 ? 'notification' : 'notifications'}.` : 'No new notifications are due.')
            })}><Clock3 size={16} /> Check now</Button>
          </div>
        </fieldset>
      </form>}
    </section>
    <section className="panel">
      <header className="panel-heading">
        <div><h2>Inbox</h2><p>{inbox ? `${inbox.unread} unread` : 'Loading notifications…'}</p></div>
        <Button variant="outline" disabled={busy || !inbox?.unread} onClick={() => void run(async () => {
          await api.readNotifications(profile.id)
        })}>Mark all read</Button>
      </header>
      <ErrorMessage error={inboxError} />
      {inbox && !inbox.items.length && <EmptyState icon={<Bell size={25} />} title="No notifications yet">
        <p>Enable notifications and confirm a due date in the Promise Ledger.</p>
        <a className="text-link" href="#/ledger">Open Promise Ledger <ChevronRight size={15} /></a>
      </EmptyState>}
      {inbox?.items.map(item => <article key={item.id} className="notification-item">
        <div className="memory-card-title">
          <h3>{item.title}</h3>
          {!item.read_at && <span className={item.kind === 'overdue' ? 'badge badge-overdue' : 'badge badge-positive'}>Unread</span>}
        </div>
        <p>{item.body}</p>
        <small><time dateTime={item.created_at}>{timeLabel(item.created_at, applied?.timezone ?? timezone)}</time></small>
        <div className="memory-inline">
          {item.promise_id !== null && <a className="text-link" href={'#/ledger/' + item.promise_id}>Open commitment <ChevronRight size={15} /></a>}
          {item.kind === 'summary' && <a className="text-link" href="#/insights">Open daily briefing <ChevronRight size={15} /></a>}
          {!item.read_at && <Button variant="ghost" disabled={busy} onClick={() => void run(async () => {
            await api.readNotifications(profile.id, item.id)
          })}>Mark read</Button>}
        </div>
        {item.can_follow_up && item.promise_id !== null && <FollowUpDraft id={item.promise_id} />}
      </article>)}
      {inbox && (offset > 0 || offset + 30 < inbox.total) && <nav className="notification-item memory-inline" aria-label="Inbox pages">
        <Button variant="outline" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>Previous</Button>
        <Button variant="outline" disabled={offset + 30 >= inbox.total} onClick={() => setOffset(offset + 30)}>Next</Button>
      </nav>}
    </section>
  </div>
}

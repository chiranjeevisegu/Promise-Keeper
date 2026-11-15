export type UserInput = { name: string; email: string }
export type User = UserInput & { id: number }
export type MeetingInput = { title: string; date: string; owner_id: number }
export type Meeting = MeetingInput & { id: number; has_transcript: boolean }
export type Transcript = { text: string; updated_at: string | null }
export type CommitmentDetails = { owner: string | null; task: string | null; recipient: string | null; deadline: string | null }
export type TranscriptLine = { id: number; quote: string; speaker: string | null; start: number | null }
export type Commitment = TranscriptLine & { label: string; scores: Record<string, number>; details: CommitmentDetails }
export type Analysis = { revision: string; status: 'review' | 'processing' | 'complete'; candidates: Commitment[]; unflagged: TranscriptLine[]; confirmed: Commitment[]; extraction_unavailable: boolean; manual_review: boolean }
export type AudioTranscript = {
  version: string
  segments: { start: number; end: number; text: string; speaker: string | null }[]
  names: Record<string, string>
}

type Source = {
  meeting_id: number; meeting_title: string; meeting_date: string; source: string
  line: number; quote: string; speaker: string | null; start: number | null
}
export type PromiseRecord = Source & CommitmentDetails & {
  id: number; task: string; kind: string; status: string; confirmed_at: string
  due_date: string | null; suggested_due_date: string | null; workflow_status: WorkflowStatus; lifecycle_version: number
}
export type WorkflowStatus = 'pending' | 'in_progress' | 'unverified' | 'completed' | 'cancelled'
export type Dependency = { id: number; task: string; meeting_title: string; owner: string | null; status: string }
type LifecycleChange = { from: string | number[] | null; to: string | number[] | null }
export type Lifecycle = {
  promise: PromiseRecord; prerequisites: Dependency[]
  deadline_suggestions: { date: string; quote: string; meeting_id: number; meeting_title: string; start: number | null }[]
  history: { id: number; actor: string; recorded_at: string; changes: Record<string, LifecycleChange>; evidence: string | null; evidence_url: string | null }[]
}
export type LifecycleUpdate = {
  actor_id: number; version: number; status: WorkflowStatus; due_date: string | null; prerequisites: number[]
  confirm_completion: boolean; evidence: string | null; evidence_url: string | null
}
export type Evidence = Source & {
  id: string; promise_id: number | null; task: string | null; owner: string | null
  deadline: string | null; due_date: string | null; status: string | null; score: number | null
}
export type Scope = {
  owner?: string; meeting_id?: number; status?: string; deadline?: string
  date_from?: string; date_to?: string; promises_only?: boolean
}
export type NotificationSettings = {
  enabled: boolean; upcoming: boolean; overdue: boolean; daily_summary: boolean
  lead_days: number; summary_time: string; timezone: string
}
export type InboxItem = {
  id: number; promise_id: number | null; kind: 'upcoming' | 'overdue' | 'summary'
  title: string; body: string; created_at: string; read_at: string | null; can_follow_up: boolean
}
export type Inbox = { items: InboxItem[]; total: number; unread: number }
export type InsightCommitment = {
  id: number; task: string; owner: string | null; meeting_id: number; meeting_title: string
  meeting_date: string; status: string; due_date: string | null
}
export type RiskItem = InsightCommitment & {
  level: 'high' | 'watch' | 'on_track' | 'insufficient' | 'closed'; score: number | null; reasons: string[]
}
export type InsightsData = {
  metrics: Record<string, number>; weeks: { week: string; confirmed: number; completed?: number; deadline_changes: number }[]
  recurring: { task: string; owner: string; meeting_count: number; commitments: InsightCommitment[] }[]
  risks: RiskItem[]; risk_total: number; graph_options: Pick<InsightCommitment, 'id' | 'task' | 'meeting_title'>[]
  owners: string[]; from: string; through: string
}
export type DependencyGraph = {
  root: number; nodes: (InsightCommitment & { level: number })[]
  edges: { source: number; target: number }[]; truncated: boolean
}
export type Briefing = {
  date: string; mode: 'ai' | 'rules'; items: RiskItem[]
  counts: { open: number; high_risk: number; overdue: number; due_today: number; undated: number }
}
export type Verdict = 'supports' | 'insufficient' | 'contradicts'
export type EvidenceReview = {
  promise: PromiseRecord
  evidence: { id: number; submitted_by: string; submitted_at: string; note: string | null; url: string | null } | null
  reviews: { id: number; reviewer: string; verdict: Verdict; note: string | null; reviewed_at: string }[]
}
export type LedgerPage = { items: PromiseRecord[]; owners: string[]; total: number }
export type RelatedPromises = { duplicates: PromiseRecord[]; related: Evidence[]; retrieval: string }
export type MemoryResult = {
  status: 'grounded' | 'retrieved' | 'insufficient' | 'generation_unavailable' | 'ledger'
  retrieval: 'hybrid' | 'keyword' | 'empty' | 'ledger'; total: number; matches: Evidence[]
  answer: { source_id: string; quote: string }[]
}

async function request<T>(path: string, method = 'GET', body?: object | FormData): Promise<T> {
  let response: Response
  try {
    const form = body instanceof FormData
    response = await fetch('/api' + path, {
      method,
      headers: body && !form ? { 'Content-Type': 'application/json' } : undefined,
      body: form ? body : body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error('Could not reach the server. Check that FastAPI is running.')
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('pk-session-expired'))
    const failure = await response.json().catch(() => null)
    if (typeof failure?.detail === 'string') throw new Error(failure.detail)
    if (Array.isArray(failure?.detail)) {
      throw new Error(failure.detail.map((issue: { loc: string[]; msg: string }) =>
        issue.loc.slice(1).join(' ') + ' — ' + issue.msg).join('. '))
    }
    throw new Error(response.status >= 500 ? 'The API is unavailable. Check that FastAPI is running.' : 'The request could not be completed.')
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const api = {
  authStatus: () => request<{ required: boolean; authenticated: boolean }>('/auth/status'),
  signIn: (password: string) => request<{ authenticated: boolean }>('/auth/login', 'POST', { password }),
  signOut: () => request<{ authenticated: boolean }>('/auth/logout', 'POST'),
  insights: (days: number, owner: string) => request<InsightsData>('/insights?' + new URLSearchParams({
    days: String(days), ...(owner ? { owner } : {}),
  })),
  dependencyGraph: (id: number) => request<DependencyGraph>(`/insights/dependencies/${id}`),
  briefing: (owner: string) => request<Briefing>('/insights/briefing', 'POST', { owner: owner || null }),
  evidenceReview: (id: number) => request<EvidenceReview>(`/promises/${id}/evidence-review`),
  saveEvidenceReview: (id: number, body: { event_id: number; revision: number; reviewer_id: number; verdict: Verdict; note: string | null }) =>
    request<EvidenceReview>(`/promises/${id}/evidence-review`, 'POST', body),
  notificationSettings: (id: number) =>
    request<{ settings: NotificationSettings; configured: boolean }>(`/profiles/${id}/notification-preferences`),
  saveNotificationSettings: (id: number, body: NotificationSettings) =>
    request<{ settings: NotificationSettings; configured: boolean }>(`/profiles/${id}/notification-preferences`, 'PUT', body),
  inbox: (id: number, offset: number) => request<Inbox>(`/profiles/${id}/notifications?offset=${offset}`),
  checkNotifications: (id: number) => request<{ created: number }>(`/profiles/${id}/notifications/check`, 'POST'),
  readNotifications: (id: number, notificationId?: number) => request<{ read: boolean }>(
    `/profiles/${id}/notifications/read` + (notificationId ? `?notification_id=${notificationId}` : ''), 'PUT',
  ),
  followUp: (id: number) => request<{ draft: string }>(`/promises/${id}/follow-up`),
  promises: (filters: Scope & { offset: number; promise_id?: number }) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    return request<LedgerPage>('/promises?' + query)
  },
  relatedPromises: (id: number) => request<RelatedPromises>(`/promises/${id}/related`),
  lifecycle: (id: number) => request<Lifecycle>(`/promises/${id}/lifecycle`),
  saveLifecycle: (id: number, body: LifecycleUpdate) => request<Lifecycle>(`/promises/${id}/lifecycle`, 'PUT', body),
  dependencyOptions: (id: number, query: string) =>
    request<Dependency[]>(`/promises/${id}/dependency-options?` + new URLSearchParams({ q: query })),
  memory: (mode: 'search' | 'ask', body: Scope & {
    query: string; profile_id?: number; previous_question?: string; as_of: string
  }) => request<MemoryResult>('/memory/' + mode, 'POST', body),
  commitments: (id: number, source: string) => request<Analysis | null>(`/meetings/${id}/commitments/${source}`),
  analyzeCommitments: (id: number, source: string, extractDetails = true, manualReview = false) =>
    request<Analysis>(`/meetings/${id}/commitments/${source}?extract_details=${extractDetails}&manual_review=${manualReview}`, 'POST'),
  confirmCommitments: (id: number, source: string, revision: string, approved: Record<string, CommitmentDetails>) =>
    request<Analysis>(`/meetings/${id}/commitments/${source}`, 'PUT', { revision, approved }),
  users: () => request<User[]>('/users'),
  saveUser: (body: UserInput, id?: number) => request<User>(id ? '/users/' + id : '/users', id ? 'PUT' : 'POST', body),
  meetings: () => request<Meeting[]>('/meetings'),
  meeting: (id: number) => request<Meeting>('/meetings/' + id),
  saveMeeting: (body: MeetingInput, id?: number) => request<Meeting>(id ? '/meetings/' + id : '/meetings', id ? 'PUT' : 'POST', body),
  deleteMeeting: (id: number) => request<void>('/meetings/' + id, 'DELETE'),
  transcript: (id: number) => request<Transcript>('/meetings/' + id + '/transcript'),
  saveTranscript: (id: number, text: string) => request<Transcript>('/meetings/' + id + '/transcript', 'PUT', { text }),
  audioCapabilities: () => request<{ diarization: boolean }>('/audio/capabilities'),
  audioTranscript: (id: number) => request<AudioTranscript | null>('/meetings/' + id + '/audio-transcript'),
  transcribe: (id: number, file: File, diarize: boolean, language: 'auto' | 'en' = 'auto') => {
    const body = new FormData()
    body.append('file', file)
    body.append('diarize', String(diarize))
    body.append('language', language)
    return request<AudioTranscript>('/meetings/' + id + '/audio-transcript', 'POST', body)
  },
  saveAudioTranscript: (id: number, entry: AudioTranscript) => {
    const speakers = entry.segments.map(segment => segment.speaker?.trim() || null)
    const used = new Set(speakers)
    return request<AudioTranscript>('/meetings/' + id + '/audio-transcript', 'PUT', {
      version: entry.version, speakers,
      texts: entry.segments.map(segment => segment.text),
      names: Object.fromEntries(Object.entries(entry.names).filter(([speaker, name]) => used.has(speaker) && name.trim())),
    })
  },
  deleteAudioTranscript: (id: number) => request<void>('/meetings/' + id + '/audio-transcript', 'DELETE'),
}

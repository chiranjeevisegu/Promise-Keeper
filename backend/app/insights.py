from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta

from sqlalchemy import select

from .models import EvidenceReview, Meeting, Promise, PromiseDependency, PromiseEvent
from .promise_dates import ACTIVE, lifecycle_status

RISK_ORDER = {"high": 0, "watch": 1, "on_track": 2, "insufficient": 3, "closed": 4}
GRAPH_LIMIT = 40


def dependency_edges(db):
    return db.execute(select(PromiseDependency.prerequisite_id, PromiseDependency.promise_id)).all()


def risk_for(promise, prerequisites, today):
    if promise.status not in ACTIVE:
        return {"level": "closed", "score": None, "reasons": []}
    blockers = [item for item in prerequisites if item.status != "completed"]
    cancelled = [item for item in blockers if item.status == "cancelled"]
    days = (promise.due_date - today).days if promise.due_date else None
    reasons = []
    score = 0
    if days is None:
        reasons.append("No confirmed due date.")
    elif days < 0:
        score += 75
        reasons.append(f"Confirmed due date passed {abs(days)} {'day' if days == -1 else 'days'} ago.")
    elif days <= 2:
        score += 25
        reasons.append("Due today." if days == 0 else f"Due in {days} {'day' if days == 1 else 'days'}.")
    if blockers:
        score += 70 if cancelled else 40
        first = blockers[0].task
        other = f" and {len(blockers) - 1} more" if len(blockers) > 1 else ""
        reasons.append(f"Unfinished prerequisite: {first}{other}.")
        if cancelled:
            reasons.append("A prerequisite was cancelled; the dependency needs review.")
    if days is not None and 0 <= days <= 2 and promise.status == "pending":
        score += 20
        reasons.append("Still marked pending close to the due date.")
    if promise.status == "unverified":
        reasons.append("Completion evidence is still being checked.")
    if days is None and not blockers:
        return {"level": "insufficient", "score": None, "reasons": reasons}
    if not reasons:
        reasons.append("A due date is recorded and no open prerequisites are linked.")
    level = "high" if score >= 65 else "watch" if score >= 25 else "on_track"
    return {"level": level, "score": min(score, 100), "reasons": reasons}


def commitment_item(promise, meeting, today):
    return {
        "id": promise.id, "task": promise.task, "owner": promise.owner, "meeting_id": meeting.id,
        "meeting_title": meeting.title, "meeting_date": meeting.date.isoformat(),
        "status": lifecycle_status(promise, today), "due_date": promise.due_date,
    }


def latest_evidence(events):
    for event in reversed(events):
        status = event.changes.get("status", {}).get("to")
        if status and status != "completed":
            break
        if event.evidence or event.evidence_url:
            return event
        if status == "completed":
            break
    return None


def completion_timing(events):
    due = None
    timing = None
    for event in events:
        if "due_date" in event.changes:
            due = event.changes["due_date"]["to"]
        status = event.changes.get("status", {}).get("to")
        if status:
            timing = None
            if status == "completed" and due:
                completed = datetime.fromisoformat(event.recorded_at).astimezone().date()
                timing = completed <= date.fromisoformat(due)
    return timing


def current_risks(db, records, today):
    by_id = {promise.id: promise for promise, _ in records}
    dependencies = defaultdict(list)
    for prerequisite, dependent in dependency_edges(db):
        if dependent in by_id:
            dependencies[dependent].append(prerequisite)
    missing = {key for keys in dependencies.values() for key in keys} - by_id.keys()
    if missing:
        by_id.update((item.id, item) for item in db.scalars(select(Promise).where(Promise.id.in_(missing))))
    items = []
    for promise, meeting in records:
        blockers = [by_id[key] for key in dependencies[promise.id] if key in by_id]
        items.append({**commitment_item(promise, meeting, today), **risk_for(promise, blockers, today)})
    return sorted(items, key=lambda item: (
        RISK_ORDER[item["level"]], -(item["score"] or 0), item["due_date"] or date.max, item["id"],
    ))


def analyze_history(db, records, today):
    by_promise = defaultdict(list)
    ids = [promise.id for promise, _ in records]
    if ids:
        for event in db.scalars(select(PromiseEvent).where(PromiseEvent.promise_id.in_(ids)).order_by(PromiseEvent.id)):
            by_promise[event.promise_id].append(event)
    latest_events = {
        promise.id: latest_evidence(by_promise[promise.id]) for promise, _ in records if promise.status == "completed"
    }
    event_ids = [event.id for event in latest_events.values() if event]
    verdicts = {}
    if event_ids:
        for review in db.scalars(select(EvidenceReview).where(EvidenceReview.event_id.in_(event_ids)).order_by(EvidenceReview.id)):
            verdicts[review.event_id] = review.verdict
    metrics = Counter()
    cohorts = defaultdict(Counter)
    groups = defaultdict(list)
    for promise, meeting in records:
        status = lifecycle_status(promise, today)
        metrics["total"] += 1
        metrics[status] += 1
        week = meeting.date - timedelta(days=meeting.date.weekday())
        cohorts[week]["confirmed"] += 1
        events = by_promise[promise.id]
        revised = sum(event.changes.get("due_date", {}).get("from") is not None for event in events)
        metrics["deadline_changes"] += revised
        cohorts[week]["deadline_changes"] += revised
        if status == "completed":
            cohorts[week]["completed"] += 1
            timing = completion_timing(events)
            if timing is not None:
                metrics["timed_completions"] += 1
                metrics["on_time"] += timing
            evidence = latest_events.get(promise.id)
            metrics["verified_completions"] += bool(evidence and verdicts.get(evidence.id) == "supports")
        if promise.owner_key and promise.task_key:
            groups[promise.owner_key, promise.task_key].append(commitment_item(promise, meeting, today))
    recurring = []
    for items in groups.values():
        meetings = {item["meeting_id"] for item in items}
        if len(meetings) >= 2:
            recurring.append({"task": items[0]["task"], "owner": items[0]["owner"],
                              "meeting_count": len(meetings), "commitments": items[:8]})
    return {
        "metrics": dict(metrics),
        "weeks": [{"week": week.isoformat(), **counts} for week, counts in sorted(cohorts.items())],
        "recurring": sorted(recurring, key=lambda row: (-row["meeting_count"], row["task"].casefold()))[:12],
    }


def graph_for(db, root, today):
    edges = dependency_edges(db)
    parents, children = defaultdict(list), defaultdict(list)
    for source, target in sorted(edges):
        children[source].append(target)
        parents[target].append(source)
    selected = {root}
    truncated = False
    pending = deque((node, links) for links in (parents, children) for node in links[root])
    while pending:
        node, links = pending.popleft()
        if node in selected:
            continue
        if len(selected) == GRAPH_LIMIT:
            truncated = True
            break
        selected.add(node)
        pending.extend((neighbor, links) for neighbor in links[node])
    level = {}
    inbound = {node: sum(parent in selected for parent in parents[node]) for node in selected}
    ready = deque(sorted(node for node in selected if inbound[node] == 0))
    while ready:
        node = ready.popleft()
        level[node] = max((level[parent] + 1 for parent in parents[node] if parent in selected), default=0)
        for child in children[node]:
            if child in selected:
                inbound[child] -= 1
                if inbound[child] == 0:
                    ready.append(child)
    nodes = [{**commitment_item(promise, meeting, today), "level": level.get(promise.id, 0)}
             for promise, meeting in db.execute(select(Promise, Meeting).join(Meeting).where(Promise.id.in_(selected)).order_by(Promise.id))]
    return {"root": root, "nodes": nodes, "edges": [{"source": source, "target": target} for source, target in edges
                                                  if source in selected and target in selected], "truncated": truncated}

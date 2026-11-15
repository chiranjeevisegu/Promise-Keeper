import hashlib
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from threading import Lock

from sqlalchemy import String, cast, select
from sqlalchemy.dialects.sqlite import insert

from .ledger import normalized
from .models import AudioTranscript, Meeting, MemoryVector, Promise, Transcript
from .promise_dates import lifecycle_status, status_column

MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models" / "all-MiniLM-L6-v2"
STOP_WORDS = frozenset(
    "a an and are about as at be been by can could did do does for from had has have how i in is it "
    "me my of on or our say said she should tell than that the their them there these they this to "
    "us was we were what when where which who will with would you your".split()
)
encoder_lock = Lock()


@dataclass(frozen=True)
class Evidence:
    id: str
    meeting_id: int
    meeting_title: str
    meeting_date: str
    source: str
    line: int
    quote: str
    speaker: str | None
    start: float | None
    promise_id: int | None = None
    task: str | None = None
    owner: str | None = None
    deadline: str | None = None
    status: str | None = None
    score: float | None = None
    continuation: str = ""
    due_date: str | None = None

    def search_text(self):
        return " ".join(str(part) for part in (
            self.task, self.quote, self.continuation, self.speaker, self.owner, self.due_date or self.deadline, self.meeting_title,
        ) if part)


def ledger_query(scope):
    query = select(Promise, Meeting).join(Meeting)
    if scope.meeting_id is not None:
        query = query.where(Promise.meeting_id == scope.meeting_id)
    if scope.owner:
        query = query.where(Promise.owner_key == normalized(scope.owner))
    if scope.status:
        query = query.where(status_column() == scope.status)
    if scope.deadline:
        query = query.where(
            Promise.deadline.icontains(scope.deadline, autoescape=True)
            | cast(Promise.due_date, String).icontains(scope.deadline, autoescape=True),
        )
    if scope.date_from:
        query = query.where(Meeting.date >= scope.date_from)
    if scope.date_to:
        query = query.where(Meeting.date <= scope.date_to)
    return query.order_by(Meeting.date.desc(), Promise.id.desc())


def chunks(quote):
    # MiniLM handles up to 256 tokens. Keep long turns in overlapping verbatim passages.
    spans = list(re.finditer(r"\S+", quote))
    for offset in range(0, len(spans), 96):
        end = min(offset + 116, len(spans))
        yield quote[spans[offset].start():spans[end - 1].end()]
        if end == len(spans):
            break


def evidence_documents(db, scope):
    docs = []
    seen = set()
    for promise, meeting in db.execute(ledger_query(scope)):
        for offset, quote in enumerate(chunks(promise.quote)):
            docs.append(Evidence(
                f"promise:{promise.id}:{offset}", meeting.id, meeting.title, meeting.date.isoformat(),
                promise.source, promise.line, quote, promise.speaker, promise.start,
                promise.id, promise.task, promise.owner, promise.deadline, lifecycle_status(promise),
                due_date=promise.due_date.isoformat() if promise.due_date else None,
            ))
            seen.add((meeting.id, promise.source, promise.start, quote))
    if scope.promises_only or scope.owner or scope.status or scope.deadline:
        return docs
    for model, source in ((Transcript, "manual"), (AudioTranscript, "audio")):
        query = select(model, Meeting).join(Meeting)
        if scope.meeting_id:
            query = query.where(Meeting.id == scope.meeting_id)
        if scope.date_from:
            query = query.where(Meeting.date >= scope.date_from)
        if scope.date_to:
            query = query.where(Meeting.date <= scope.date_to)
        for record, meeting in db.execute(query.order_by(Meeting.date.desc(), Meeting.id.desc())):
            if source == "audio":
                items = []
                for index, row in enumerate(record.segments):
                    speaker = row.get("speaker")
                    continuation = ""
                    if index + 1 < len(record.segments) and not row["text"].rstrip().endswith((".", "?", "!", "…")):
                        following = record.segments[index + 1]
                        if speaker == following.get("speaker") and following["start"] - row["end"] < 2:
                            continuation = following["text"][:250]
                    items.append((row["text"], record.names.get(speaker, speaker), row["start"], continuation))
            else:
                items = []
                for line in record.text.splitlines():
                    speaker, separator, quote = line.partition(":")
                    if separator and 0 < len(speaker.strip()) <= 40:
                        items.append((quote.strip(), speaker.strip(), None, ""))
                    else:
                        items.append((line.strip(), None, None, ""))
            for line, (text, speaker, start, continuation) in enumerate(items):
                for offset, quote in enumerate(chunks(text)):
                    if (meeting.id, source, start, quote) not in seen:
                        docs.append(Evidence(
                            f"{source}:{meeting.id}:{line}:{offset}", meeting.id, meeting.title,
                            meeting.date.isoformat(), source, line, quote, speaker, start, continuation=continuation,
                        ))
    return docs


@lru_cache(maxsize=1)
def encoder(path):
    from sentence_transformers import SentenceTransformer
    import torch

    torch.set_num_threads(min(4, torch.get_num_threads()))
    return SentenceTransformer(path, device="cpu", local_files_only=True, trust_remote_code=False)


def encode(texts):
    path = os.getenv("PROMISE_KEEPER_EMBEDDING_MODEL", str(MODEL_DIR))
    with encoder_lock:
        return encoder(path).encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)


def semantic_scores(db, docs, query):
    import faiss
    import numpy as np

    path = Path(os.getenv("PROMISE_KEEPER_EMBEDDING_MODEL", str(MODEL_DIR)))
    model = path / "model.safetensors"
    signature = f"{path.resolve()}:{model.stat().st_mtime_ns}"
    terms = [doc.search_text() for doc in docs]
    hashes = [hashlib.sha256(f"{signature}\n{text}".encode()).hexdigest() for text in terms]
    stored = {row.source_key: (row.fingerprint, row.vector) for row in db.scalars(select(MemoryVector))}
    db.rollback()
    missing = [i for i, doc in enumerate(docs) if stored.get(doc.id, (None,))[0] != hashes[i]]
    vectors = encode([query, *(terms[i] for i in missing)])
    query_vector = np.asarray(vectors[:1], dtype=np.float32)
    dimension = query_vector.shape[1]
    for offset, i in enumerate(missing, start=1):
        stored[docs[i].id] = (hashes[i], np.asarray(vectors[offset], dtype="<f4").tobytes())
    matrix = np.stack([np.frombuffer(stored[doc.id][1], dtype="<f4") for doc in docs])
    if matrix.shape != (len(docs), dimension) or not np.isfinite(matrix).all():
        raise ValueError("Stored embeddings are invalid.")
    index = faiss.IndexFlatIP(dimension)
    index.add(np.ascontiguousarray(matrix, dtype=np.float32))
    similarity, locations = index.search(query_vector, len(docs))
    scores = np.empty(len(docs), dtype=np.float32)
    scores[locations[0]] = similarity[0]

    # Only cache vectors for meetings that still exist after encoding.
    if missing:
        active = set(db.scalars(select(Meeting.id)))
        for i in missing:
            doc = docs[i]
            if doc.meeting_id in active:
                statement = insert(MemoryVector).values(
                    source_key=doc.id, meeting_id=doc.meeting_id, fingerprint=hashes[i], vector=stored[doc.id][1],
                )
                db.execute(statement.on_conflict_do_update(
                    index_elements=["source_key"],
                    set_={"fingerprint": statement.excluded.fingerprint, "vector": statement.excluded.vector},
                ))
        db.commit()
    return scores.tolist()


def tokens(text):
    return set(normalized(text).split()) - STOP_WORDS


def retrieve(db, docs, query, limit):
    if not docs:
        return [], "empty"
    query_terms = tokens(query)
    contents = [tokens(doc.search_text()) for doc in docs]
    frequencies = Counter(term for content in contents for term in content & query_terms)
    weights = {term: math.log(1 + len(docs) / (1 + frequencies[term])) for term in query_terms}
    total = sum(weights.values()) or 1
    try:
        similarities = semantic_scores(db, docs, query)
        mode = "hybrid"
    except (ImportError, OSError, RuntimeError, ValueError):
        similarities = [0.] * len(docs)
        mode = "keyword"
    ranked = []
    for doc, content, semantic in zip(docs, contents, similarities, strict=True):
        lexical = sum(weights[term] for term in content & query_terms) / total
        if semantic < .34 and lexical < .27:
            continue
        score = .75 * max(0., semantic) + .25 * lexical if mode == "hybrid" else lexical
        ranked.append(replace(doc, score=round(score, 4)))
    return sorted(ranked, key=lambda doc: (doc.score, doc.promise_id is not None), reverse=True)[:limit], mode

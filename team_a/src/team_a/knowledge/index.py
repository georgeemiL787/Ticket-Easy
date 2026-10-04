"""Build and load the per-tenant knowledge index.

Layout under var/index/<tenant_id>/:
  passages.jsonl, passages.npy   policy/FAQ passages and their unit-length embeddings
  tickets.jsonl,  tickets.npy    past tickets and their embeddings
  meta.json                      model, counts, corpus hash (for reproducibility checks)
The .npy files are absent when the index was built with --no-embeddings (keyword-only mode).
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from team_a.config import settings
from team_a.knowledge.bm25 import BM25
from team_a.knowledge.embeddings import Embedder
from team_a.knowledge.parsers import Section, parse
from team_a.text import detect_language, tokenize

MAX_PASSAGE_CHARS = 1500


class IndexNotBuilt(RuntimeError):
    pass


class TenantNotFound(RuntimeError):
    pass


def _split_long(section: Section) -> list[Section]:
    if len(section.text) <= MAX_PASSAGE_CHARS:
        return [section]
    parts, current = [], ""
    for para in section.text.split("\n"):
        if current and len(current) + len(para) > MAX_PASSAGE_CHARS:
            parts.append(current)
            current = ""
        current = f"{current}\n{para}".strip()
    parts.append(current)
    return [Section(f"{section.key}-p{i}", section.title, p) for i, p in enumerate(parts, 1)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_manifest(tenant_id: str) -> dict:
    path = settings.corpus_dir(tenant_id) / "manifest.json"
    if not path.exists():
        raise TenantNotFound(f"No corpus manifest for tenant '{tenant_id}' at {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("tenant_id") != tenant_id:
        raise ValueError(f"manifest tenant_id {manifest.get('tenant_id')!r} != {tenant_id!r}")
    current = [d["document_id"] for d in manifest["documents"] if d.get("current")]
    duplicates = {d for d in current if current.count(d) > 1}
    if duplicates:
        raise ValueError(f"More than one current version for: {sorted(duplicates)}")
    return manifest


def build_passages(tenant_id: str) -> tuple[list[dict], dict[str, str]]:
    manifest = load_manifest(tenant_id)
    corpus = settings.corpus_dir(tenant_id)
    records, hashes = [], {}
    for doc in manifest["documents"]:
        path = corpus / doc["file"]
        hashes[doc["file"]] = _sha(path)
        for section in (s for sec in parse(path) for s in _split_long(sec)):
            citation = f"{doc['document_id']}@{doc['version']}#{section.key}"
            records.append({
                "passage_id": citation,
                "tenant_id": tenant_id,
                "document_id": doc["document_id"],
                "version": doc["version"],
                "current": bool(doc.get("current")),
                "effective_date": doc.get("effective_date"),
                "section": section.title or section.key,
                "language": detect_language(section.text),
                "text": section.text,
                "citation": citation,
            })
    seen = set()
    for r in records:
        if r["passage_id"] in seen:
            raise ValueError(f"Duplicate passage id {r['passage_id']}; check section numbering")
        seen.add(r["passage_id"])
    return records, hashes


def load_tickets(tenant_id: str) -> list[dict]:
    path = settings.tickets_file(tenant_id)
    if not path.exists():
        return []
    tickets = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for t in tickets:
        if t.get("tenant_id") != tenant_id:
            raise ValueError(f"Ticket {t.get('ticket_id')} belongs to another tenant")
    return tickets


def passage_embed_text(r: dict) -> str:
    return f"{r['section']}\n{r['text']}"


def ticket_embed_text(t: dict) -> str:
    return f"{t['customer_message']}\n{t['resolution']}"


def build_index(tenant_id: str, embedder: Embedder | None) -> dict:
    records, hashes = build_passages(tenant_id)
    tickets = load_tickets(tenant_id)
    out = settings.tenant_index_dir(tenant_id)
    out.mkdir(parents=True, exist_ok=True)

    _write_jsonl(out / "passages.jsonl", records)
    _write_jsonl(out / "tickets.jsonl", tickets)
    for name in ("passages.npy", "tickets.npy"):
        (out / name).unlink(missing_ok=True)
    if embedder is not None:
        np.save(out / "passages.npy", embedder.embed([passage_embed_text(r) for r in records]))
        if tickets:
            np.save(out / "tickets.npy", embedder.embed([ticket_embed_text(t) for t in tickets]))

    meta = {
        "tenant_id": tenant_id,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "embed_model": embedder.model if embedder else None,
        "passages": len(records),
        "current_passages": sum(r["current"] for r in records),
        "tickets": len(tickets),
        "source_hashes": hashes,
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class TenantIndex:
    tenant_id: str
    meta: dict
    passages: list[dict]
    passage_vectors: np.ndarray | None
    passage_bm25: BM25
    tickets: list[dict]
    ticket_vectors: np.ndarray | None
    ticket_bm25: BM25
    by_citation: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, tenant_id: str) -> "TenantIndex":
        root = settings.tenant_index_dir(tenant_id)
        if not (root / "meta.json").exists():
            if not settings.corpus_dir(tenant_id).exists():
                raise TenantNotFound(f"Unknown tenant '{tenant_id}'")
            raise IndexNotBuilt(f"Index for '{tenant_id}' not built. Run: python -m team_a ingest --tenant {tenant_id}")
        passages = _read_jsonl(root / "passages.jsonl")
        tickets = _read_jsonl(root / "tickets.jsonl")
        pv = np.load(root / "passages.npy") if (root / "passages.npy").exists() else None
        tv = np.load(root / "tickets.npy") if (root / "tickets.npy").exists() else None
        return cls(
            tenant_id=tenant_id,
            meta=json.loads((root / "meta.json").read_text(encoding="utf-8")),
            passages=passages,
            passage_vectors=pv,
            passage_bm25=BM25([tokenize(passage_embed_text(p)) for p in passages]),
            tickets=tickets,
            ticket_vectors=tv,
            ticket_bm25=BM25([tokenize(ticket_embed_text(t)) for t in tickets]),
            by_citation={p["citation"]: p for p in passages},
        )

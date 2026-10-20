"""The documents of a business added through the admin API: upload, status, remove, and an atomic re-index.

Layout under var/tenants/<tenant_id>/:
  corpus/manifest.json     the documents that were read successfully (the same format as the shipped tenants)
  corpus/<file>            the uploaded files (versions of one document sit side by side)
  documents.json           one record per upload with its status: uploaded | parsing | indexed | failed | removed
A document that cannot be read is recorded as `failed` with the reason and is never put in the manifest, so it can not
stop the others from being indexed. Uploading the same document id again adds a new version; the older ones stay in the
manifest as superseded (current: false), as in the shipped corpora.
"""

import hashlib
import json
import re
import shutil
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from knowledge_service.config import settings
from knowledge_service.search.embeddings import EmbeddingUnavailable, OllamaEmbedder
from knowledge_service.search.index import build_index, swap_index
from knowledge_service.search.parsers import PARSERS, parse

STATUSES = ("uploaded", "parsing", "indexed", "failed", "removed")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,120}$")
_locks: dict[str, threading.Lock] = {}


class DocumentError(ValueError):
    """The upload or request cannot be accepted; the message says why."""

    def __init__(self, message: str, status: int = 422, code: str = "INVALID_REQUEST") -> None:
        super().__init__(message)
        self.status, self.code = status, code


def tenant_lock(tenant_id: str) -> threading.Lock:
    return _locks.setdefault(tenant_id, threading.Lock())


def _slug(name: str) -> str:
    stem = Path(name).stem.lower()
    return re.sub(r"[^a-z0-9]+", "_", stem).strip("_")[:60] or "document"


class DocumentLibrary:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.root = settings.onboarded_dir / tenant_id
        self.corpus = self.root / "corpus"
        self.manifest_path = self.corpus / "manifest.json"
        self.records_path = self.root / "documents.json"

    # ---- state ----

    def exists(self) -> bool:
        return self.root.is_dir()

    def _read(self) -> list[dict]:
        if not self.records_path.exists():
            return []
        return json.loads(self.records_path.read_text(encoding="utf-8"))

    def _write(self, records: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.records_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.records_path)

    def _manifest(self) -> dict:
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {"tenant_id": self.tenant_id, "documents": []}

    def _write_manifest(self, manifest: dict) -> None:
        self.corpus.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def records(self) -> list[dict]:
        """Every upload, newest first, with its status."""
        return sorted(self._read(), key=lambda r: r["uploaded_at"], reverse=True)

    # ---- upload / remove ----

    def add(self, filename: str, content: bytes, document_id: str | None = None, effective_date: date | None = None) -> dict:
        if settings.category(self.tenant_id) is not None:
            raise DocumentError("this business ships with its own documents and is not changed through the API", 409, "INVALID_STATE")
        suffix = Path(filename).suffix.lower()
        if suffix not in PARSERS:
            raise DocumentError(f"unsupported file type {suffix or '(none)'}: use {', '.join(sorted(PARSERS))}")
        if not content:
            raise DocumentError("the file is empty")
        if len(content) > settings.max_upload_bytes:
            raise DocumentError(f"the file is larger than {settings.max_upload_bytes // (1024 * 1024)} MB", 413)
        base = Path(filename).name
        if not SAFE_NAME.match(base):
            raise DocumentError("use a plain file name (letters, numbers, spaces, dots, dashes)")
        doc_id = document_id or _slug(base)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", doc_id):
            raise DocumentError("document_id must be lowercase letters, numbers and underscores")
        records = self._read()
        version = 1 + max((r["version_number"] for r in records if r["document_id"] == doc_id), default=0)
        stored = f"{doc_id}.v{version}{suffix}"
        self.corpus.mkdir(parents=True, exist_ok=True)
        path = self.corpus / stored
        path.write_bytes(content)
        record = {
            "document_id": doc_id, "version_number": version, "version": f"v{version}", "file": stored, "filename": base,
            "sha256": hashlib.sha256(content).hexdigest()[:16], "size": len(content), "status": "uploaded", "error": None,
            "sections": 0, "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "effective_date": (effective_date or date.today()).isoformat(),
        }
        try:
            record["sections"] = len(parse(path))
            if record["sections"] == 0:
                raise ValueError("no readable text was found in this file")
        except Exception as exc:  # a broken, encrypted or empty file: reported on the record, never indexed
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
        records.append(record)
        self._write(records)
        if record["status"] != "failed":
            manifest = self._manifest()
            for doc in manifest["documents"]:
                if doc["document_id"] == doc_id:
                    doc["current"] = False  # the new version supersedes the older ones
            manifest["documents"].append({"document_id": doc_id, "version": record["version"], "file": stored, "current": True,
                                          "effective_date": record["effective_date"]})
            self._write_manifest(manifest)
        return record

    def remove(self, document_id: str) -> int:
        """Mark every version of this document as no longer current (it stays on file). Returns how many versions."""
        records = self._read()
        hit = [r for r in records if r["document_id"] == document_id and r["status"] != "removed"]
        if not hit:
            raise DocumentError(f"no document '{document_id}'", 404, "NOT_FOUND")
        for r in hit:
            r["status"] = "removed"
        manifest = self._manifest()
        for doc in manifest["documents"]:
            if doc["document_id"] == document_id:
                doc["current"] = False
        self._write(records)
        self._write_manifest(manifest)
        return len(hit)

    # ---- re-index ----

    def reindex(self, embed: bool = True) -> dict:
        """Read every listed document, build a new index beside the live one and swap it in. Per document status is kept:
        a document that no longer reads is marked failed and left out. Returns a summary for the job record."""
        with tenant_lock(self.tenant_id):
            records = self._read()
            manifest = self._manifest()
            for r in records:
                if r["status"] in ("uploaded", "indexed", "parsing"):
                    r["status"] = "parsing"
            self._write(records)
            keep, problems = [], []
            for doc in manifest["documents"]:
                record = next((r for r in records if r["file"] == doc["file"]), None)
                try:
                    if not parse(self.corpus / doc["file"]):
                        raise ValueError("no readable text was found in this file")
                    keep.append(doc)
                except Exception as exc:
                    problems.append(f"{doc['file']}: {exc}")
                    if record is not None:
                        record.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
            manifest["documents"] = keep
            self._write_manifest(manifest)
            if not any(d.get("current") for d in keep):
                for r in records:
                    if r["status"] == "parsing":
                        r["status"] = "uploaded"
                self._write(records)
                raise DocumentError("there is no current document to index", 409, "INVALID_STATE")
            scratch = settings.tenant_index_dir(self.tenant_id).with_name(self.tenant_id + ".building")
            shutil.rmtree(scratch, ignore_errors=True)
            embedder, embedded = (OllamaEmbedder() if embed else None), True
            try:
                try:
                    meta = build_index(self.tenant_id, embedder, out=scratch)
                except EmbeddingUnavailable:
                    shutil.rmtree(scratch, ignore_errors=True)
                    embedded = False
                    meta = build_index(self.tenant_id, None, out=scratch)
                swap_index(self.tenant_id, scratch)
            except Exception:
                shutil.rmtree(scratch, ignore_errors=True)
                for r in records:
                    if r["status"] == "parsing":
                        r["status"] = "uploaded"
                self._write(records)
                raise
            listed = {d["file"] for d in keep}
            for r in records:
                if r["status"] == "parsing":
                    r["status"] = "indexed" if r["file"] in listed else r["status"]
                    r["error"] = None
            self._write(records)
            return {"passages": meta["passages"], "current_passages": meta["current_passages"], "embeddings": embedded and embedder is not None,
                    "failed": problems, "built_at": meta["built_at"]}

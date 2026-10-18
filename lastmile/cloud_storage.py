"""Optional private Cloud Storage persistence for the single-instance cloud demo.

Incidents use object-generation compare-and-swap, not SQLite on a FUSE mount.
The legacy warning/subscriber stores retain their process lock and require ONE
Cloud Run instance; their files are mirrored and restored across replacement.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from google.api_core.exceptions import NotFound, PreconditionFailed

from .store import Store
from .subscriptions import SubscriptionStore
from .workspace import WorkspaceError, WorkspaceStore, now_iso


class CloudFiles:
    def __init__(self, bucket_name, directory, bucket=None):
        if bucket is None:
            from google.cloud import storage
            bucket = storage.Client().bucket(bucket_name)
        self.bucket = bucket
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def restore(self, prefix):
        for blob in self.bucket.list_blobs(prefix="state/" + prefix, timeout=30):
            relative = blob.name.removeprefix("state/")
            target = (self.directory / relative).resolve()
            if not target.is_relative_to(self.directory) or target == self.directory:
                raise ValueError("Cloud state object has an invalid file path")
            target.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(target), timeout=30)

    def save(self, path):
        path = Path(path).resolve()
        relative = path.relative_to(self.directory).as_posix()
        self.bucket.blob("state/" + relative).upload_from_filename(str(path), timeout=30)


class CloudWorkspaceStore(WorkspaceStore):
    def __init__(self, files):
        self.bucket = files.bucket
        self.lock = threading.RLock()

    def close(self):
        pass

    def _read(self, incident_id):
        if not re.fullmatch(r"incident_[a-f0-9]{12}", incident_id):
            raise WorkspaceError("Incident not found.", 404)
        for attempt in range(4):
            # A fresh handle each time: a reused one remembers the old generation and would ask for it again.
            blob = self.bucket.blob("incidents/" + incident_id + ".json")
            try:
                blob.reload(timeout=30)
            except NotFound:
                raise WorkspaceError("Incident not found.", 404) from None
            generation = int(blob.generation)
            try:
                doc = json.loads(blob.download_as_text(if_generation_match=generation, timeout=30))
                return doc, blob, generation
            except (NotFound, PreconditionFailed):
                # Saved again between the metadata read and the download (e.g. a background suggestion): read again.
                if attempt == 3:
                    raise WorkspaceError("This incident changed while loading. Refresh before saving.") from None

    def get(self, incident_id):
        return self._read(incident_id)[0]

    def _all(self):
        """Every incident as it is now. A listed object is read at its latest generation: one saved between
        listing and reading would otherwise 404 at the listed generation. One deleted meanwhile is skipped."""
        docs = []
        for listed in self.bucket.list_blobs(prefix="incidents/", timeout=30):
            try:
                docs.append(json.loads(self.bucket.blob(listed.name).download_as_text(timeout=30)))
            except NotFound:
                continue
        return docs

    def list(self):
        docs = self._all()
        return sorted([{"id": d["id"], "title": d["title"], "version": d["version"], "updated_at": d["updated_at"],
                        "status": d["status"], "exercise": True} for d in docs if not d.get("internal") and not d.get("coordination")], key=lambda d: d["updated_at"], reverse=True)

    def insert_document(self, doc):
        self.bucket.blob("incidents/" + doc["id"] + ".json").upload_from_string(
            json.dumps(doc), content_type="application/json", if_generation_match=0, timeout=30)
        return doc

    def ensure_internal(self, doc):
        try:
            return self.insert_document(doc)
        except PreconditionFailed:
            return self.get(doc["id"])

    def team_documents(self):
        return [d for d in self._all() if d.get("coordination") and not d.get("internal")]

    def create(self, title, description=""):
        doc = self.new_document(title, description)
        self.bucket.blob("incidents/" + doc["id"] + ".json").upload_from_string(
            json.dumps(doc), content_type="application/json", if_generation_match=0, timeout=30)
        return doc

    def mutate(self, incident_id, version, actor, fn, *, metadata=False):
        if not actor or not actor.strip():
            raise WorkspaceError("Enter a reviewer name. Names are self-declared in this exercise.", 422)
        with self.lock:
            doc, blob, generation = self._read(incident_id)
            if doc["version"] != version:
                raise WorkspaceError("This incident changed. Refresh it before saving.")
            fn(doc)
            if not metadata:
                doc["version"] += 1
            doc["updated_at"] = now_iso()
            try:
                blob.upload_from_string(json.dumps(doc), content_type="application/json",
                                        if_generation_match=generation, timeout=30)
            except PreconditionFailed:
                raise WorkspaceError("Another reviewer changed this incident. Refresh before saving.") from None
            return doc


class CloudAlertStore(Store):
    def __init__(self, files):
        self.cloud = files
        files.restore("runs/")
        files.restore("published.json")
        super().__init__(files.directory)

    def put(self, run):
        with self._lock:
            super().put(run)
            self.cloud.save(self.dir / "runs" / (run.id + ".json"))
            return run

    def _save_published(self):
        super()._save_published()
        self.cloud.save(self.dir / "published.json")

    def write_outbox(self, run_id, name, content):
        path = super().write_outbox(run_id, name, content)
        self.cloud.save(path)
        return path


class CloudSubscriptionStore(SubscriptionStore):
    def __init__(self, files):
        self.cloud = files
        files.restore("subscribers.json")
        files.restore("secret.key")
        super().__init__(files.directory)
        if (files.directory / "secret.key").is_file():
            files.save(files.directory / "secret.key")

    def _save(self):
        super()._save()
        self.cloud.save(self.path)

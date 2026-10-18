"""Incident document store (SQLite transactions, checked versions) and shared record helpers."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:12]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        raise WorkspaceError("Time must be ISO 8601 with a timezone.", 422) from None


class WorkspaceError(ValueError):
    def __init__(self, detail, status=409):
        super().__init__(detail)
        self.status = status


HAZARDS = [
    ("landslide", "Landslide / slope failure", r"landslide|slope failure"),
    ("river_flood", "River flood", r"river flood|flood warning|flooding"),
    ("flash_flood", "Flash flood", r"flash flood"),
    ("urban_flood", "Urban flood", r"urban flood"),
    ("heavy_rain", "Heavy rain", r"heavy rain warning"),
    ("lightning", "Thunderstorm / lightning", r"thunderstorm|lightning warning"),
    ("wind", "Strong wind", r"strong wind warning"),
    ("cyclone", "Cyclone / tropical storm", r"cyclone|tropical storm"),
    ("storm_surge", "Storm surge / coastal inundation", r"storm surge|coastal inundation"),
    ("tsunami", "Tsunami", r"tsunami"),
    ("marine", "Rough sea / marine weather", r"rough sea|marine warning"),
    ("drought", "Drought", r"drought"),
    ("heat", "Extreme heat", r"extreme heat|heatwave"),
    ("wildfire", "Wildfire / forest fire", r"wildfire|forest fire"),
    ("earthquake", "Earthquake", r"earthquake"),
    ("health", "Public-health outbreak", r"outbreak|epidemic"),
    ("chemical", "Industrial / chemical incident", r"chemical leak|industrial incident"),
    ("air", "Air pollution", r"air pollution warning"),
    ("other", "Other official warning", r"(?!)"),
]

def record_event(doc, actor, action, detail, source_id=None):
    doc["events"].append({"id": uid("event"), "at": now_iso(), "actor": actor, "action": action,
                          "detail": detail, "source_id": source_id})


class WorkspaceStore:
    def __init__(self, path=":memory:"):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self.lock = threading.RLock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.connection.commit()

    def close(self):
        with self.lock:
            self.connection.close()

    def list(self):
        with self.lock:
            docs = [json.loads(r[0]) for r in self.connection.execute("SELECT body FROM incidents")]
        return sorted([{"id": d["id"], "title": d["title"], "version": d["version"], "updated_at": d["updated_at"],
                        "status": d["status"], "exercise": True} for d in docs if not d.get("internal") and not d.get("coordination")], key=lambda d: d["updated_at"], reverse=True)

    def insert_document(self, doc):
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO incidents VALUES (?,?)", (doc["id"], json.dumps(doc)))
        return copy.deepcopy(doc)

    def ensure_internal(self, doc):
        with self.lock, self.connection:
            self.connection.execute("INSERT OR IGNORE INTO incidents VALUES (?,?)", (doc["id"], json.dumps(doc)))
        return self.get(doc["id"])

    def team_documents(self):
        with self.lock:
            return [d for (raw,) in self.connection.execute("SELECT body FROM incidents")
                    if (d := json.loads(raw)).get("coordination") and not d.get("internal")]

    def get(self, incident_id):
        with self.lock:
            row = self.connection.execute("SELECT body FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if row is None:
            raise WorkspaceError("Incident not found.", 404)
        return json.loads(row[0])

    def create(self, title, description=""):
        doc = self.new_document(title, description)
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO incidents VALUES (?,?)", (doc["id"], json.dumps(doc)))
        return copy.deepcopy(doc)

    @classmethod
    def new_document(cls, title, description=""):
        now = now_iso()
        doc = {"id": uid("incident"), "title": title, "description": description, "country": "lk", "exercise": True,
               "status": "active", "version": 1, "created_at": now, "updated_at": now, "centres": [],
               "cases": [{"id": "warning-case", "title": "Warning coordination", "area": "Sri Lanka", "kind": "warning"},
                         {"id": "centre-case", "title": "Centre readiness & local needs", "area": "Pilot area", "kind": "centre"}],
               "sources": [], "tasks": [], "needs": [], "communications": [], "handovers": [], "events": []}
        record_event(doc, "Exercise coordinator", "Incident opened", "Fictional exercise workspace. No real alerts or admissions.")
        return doc

    def mutate(self, incident_id, version, actor, fn, *, metadata=False):
        if not actor or not actor.strip():
            raise WorkspaceError("Enter a reviewer name. Names are self-declared in this exercise.", 422)
        with self.lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                doc = self.get(incident_id)
                if version != doc["version"]:
                    raise WorkspaceError("This incident changed. Refresh it and review the latest evidence before saving.")
                fn(doc)
                if not metadata:
                    doc["version"] += 1
                doc["updated_at"] = now_iso()
                self.connection.execute("UPDATE incidents SET body=? WHERE id=?", (json.dumps(doc), incident_id))
                self.connection.commit()
                return doc
            except Exception:
                self.connection.rollback()
                raise

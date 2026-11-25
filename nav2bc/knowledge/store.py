"""Persistence: projects, report analyses, knowledge base (changes) and error signatures.

SQLite for development; the schema stays portable to PostgreSQL.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signature import normalize, signature_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    client TEXT,
    scenario TEXT NOT NULL DEFAULT 'B',
    source_version TEXT NOT NULL,
    target_version INTEGER NOT NULL,
    target_online INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    report_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    UNIQUE (project_id, report_id)
);
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version TEXT, target_version TEXT,
    source_table TEXT, source_field TEXT, target_table TEXT, target_field TEXT,
    change_type TEXT NOT NULL,
    nature TEXT NOT NULL DEFAULT 'structure',
    description TEXT NOT NULL,
    documentation_url TEXT,
    evidence TEXT NOT NULL DEFAULT 'hypothesis',
    required_action TEXT,
    risk TEXT NOT NULL DEFAULT 'medium',
    confidence REAL NOT NULL DEFAULT 0.5,
    validation_status TEXT NOT NULL DEFAULT 'proposed',
    scope TEXT NOT NULL DEFAULT 'project',
    observations TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    validated_by TEXT, validated_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS error_signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    module TEXT NOT NULL,
    normalized TEXT NOT NULL,
    example_message TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 0,
    project_count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    solution TEXT, root_cause TEXT, solution_status TEXT,
    successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS error_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature_id INTEGER NOT NULL REFERENCES error_signatures(id) ON DELETE CASCADE,
    project_id INTEGER,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

ENTRY_FIELDS = (
    "source_version", "target_version", "source_table", "source_field", "target_table", "target_field",
    "change_type", "nature", "description", "documentation_url", "evidence", "required_action", "risk",
    "confidence", "scope", "observations",
)
EVIDENCES = {"documented", "id_identity", "hypothesis", "consultant"}
SCOPES = {"microsoft", "version", "client", "project"}
STATUSES = {"proposed", "reviewed", "validated", "rejected"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ValidationError(ValueError):
    pass


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def _exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ projects
    def create_project(self, name: str, source_version: str, target_version: int, client: str | None = None,
                       scenario: str = "B", target_online: bool = True) -> dict:
        cur = self._exec(
            "INSERT INTO projects (name, client, scenario, source_version, target_version, target_online, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (name, client, scenario, source_version, target_version, int(target_online), _now()))
        return self.get_project(cur.lastrowid)

    def get_project(self, project_id: int) -> dict | None:
        p = self._one("SELECT * FROM projects WHERE id=?", (project_id,))
        if p:
            p["target_online"] = bool(p["target_online"])
        return p

    def list_projects(self) -> list[dict]:
        rows = self._all(
            "SELECT p.*, (SELECT COUNT(*) FROM report_analyses r WHERE r.project_id=p.id) AS report_count "
            "FROM projects p ORDER BY p.id DESC")
        for p in rows:
            p["target_online"] = bool(p["target_online"])
        return rows

    # ------------------------------------------------------------------ report analyses
    def save_report_analysis(self, project_id: int, analysis: dict) -> None:
        self._exec(
            "INSERT INTO report_analyses (project_id, report_id, name, data, analyzed_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(project_id, report_id) DO UPDATE SET name=excluded.name, data=excluded.data, "
            "analyzed_at=excluded.analyzed_at",
            (project_id, analysis["report_id"], analysis["name"], json.dumps(analysis), _now()))

    def list_report_analyses(self, project_id: int) -> list[dict]:
        rows = self._all("SELECT data, analyzed_at FROM report_analyses WHERE project_id=? ORDER BY report_id",
                         (project_id,))
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d["analyzed_at"] = r["analyzed_at"]
            out.append(d)
        return out

    def get_report_analysis(self, project_id: int, report_id: int) -> dict | None:
        r = self._one("SELECT data FROM report_analyses WHERE project_id=? AND report_id=?", (project_id, report_id))
        return json.loads(r["data"]) if r else None

    # ------------------------------------------------------------------ knowledge base
    def add_entry(self, data: dict[str, Any]) -> dict:
        entry = {k: data.get(k) for k in ENTRY_FIELDS}
        if not entry.get("change_type") or not entry.get("description"):
            raise ValidationError("change_type et description sont obligatoires")
        entry["evidence"] = entry.get("evidence") or ("documented" if entry.get("documentation_url") else "hypothesis")
        if entry["evidence"] not in EVIDENCES:
            raise ValidationError(f"evidence doit être parmi {sorted(EVIDENCES)}")
        if entry["evidence"] == "documented" and not entry.get("documentation_url"):
            raise ValidationError("Une entrée « documented » doit citer sa documentation_url")
        entry["scope"] = entry.get("scope") or "project"
        if entry["scope"] not in SCOPES:
            raise ValidationError(f"scope doit être parmi {sorted(SCOPES)}")
        entry["nature"] = entry.get("nature") or "structure"
        entry["risk"] = entry.get("risk") or "medium"
        entry["confidence"] = float(entry["confidence"]) if entry.get("confidence") is not None else (
            0.9 if entry["evidence"] in ("documented", "id_identity") else 0.3)
        now = _now()
        cols = ", ".join(ENTRY_FIELDS) + ", created_at, updated_at"
        marks = ", ".join("?" for _ in range(len(ENTRY_FIELDS) + 2))
        cur = self._exec(f"INSERT INTO knowledge_entries ({cols}) VALUES ({marks})",
                         tuple(entry[k] for k in ENTRY_FIELDS) + (now, now))
        return self.get_entry(cur.lastrowid)

    def get_entry(self, entry_id: int) -> dict | None:
        e = self._one("SELECT * FROM knowledge_entries WHERE id=?", (entry_id,))
        if e:
            e["auto_applicable"] = self._auto_applicable(e)
            e["history"] = self._all("SELECT version, data, changed_at FROM knowledge_history WHERE entry_id=? "
                                     "ORDER BY version", (entry_id,))
            for h in e["history"]:
                h["data"] = json.loads(h["data"])
        return e

    @staticmethod
    def _auto_applicable(e: dict) -> bool:
        # Never automatic for a hypothesis that nobody validated, nor for anything not validated.
        return e["validation_status"] == "validated" and e["evidence"] in ("documented", "id_identity", "consultant")

    def list_entries(self, **filters: Any) -> list[dict]:
        clauses, params = [], []
        for key in ("scope", "validation_status", "evidence", "change_type", "source_table", "target_version"):
            if filters.get(key):
                clauses.append(f"{key}=?")
                params.append(filters[key])
        if filters.get("q"):
            clauses.append("(description LIKE ? OR source_table LIKE ? OR source_field LIKE ? OR target_field LIKE ?)")
            params += [f"%{filters['q']}%"] * 4
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._all(f"SELECT * FROM knowledge_entries{where} ORDER BY id", tuple(params))
        for r in rows:
            r["auto_applicable"] = self._auto_applicable(r)
        return rows

    def update_entry(self, entry_id: int, changes: dict[str, Any]) -> dict:
        current = self._one("SELECT * FROM knowledge_entries WHERE id=?", (entry_id,))
        if not current:
            raise KeyError(entry_id)
        updates = {k: v for k, v in changes.items() if k in ENTRY_FIELDS}
        if updates.get("evidence") == "documented" and not (updates.get("documentation_url") or current["documentation_url"]):
            raise ValidationError("Une entrée « documented » doit citer sa documentation_url")
        self._exec("INSERT INTO knowledge_history (entry_id, version, data, changed_at) VALUES (?,?,?,?)",
                   (entry_id, current["version"], json.dumps(current), _now()))
        # Any content change sends the entry back to review.
        sets = ", ".join(f"{k}=?" for k in updates)
        sets = (sets + ", " if sets else "") + "version=version+1, validation_status='proposed', " \
               "validated_by=NULL, validated_at=NULL, updated_at=?"
        self._exec(f"UPDATE knowledge_entries SET {sets} WHERE id=?", tuple(updates.values()) + (_now(), entry_id))
        return self.get_entry(entry_id)

    def set_entry_status(self, entry_id: int, status: str, by: str | None = None) -> dict:
        if status not in STATUSES:
            raise ValidationError(f"status doit être parmi {sorted(STATUSES)}")
        if status == "validated" and not by:
            raise ValidationError("Une validation est nominative : indiquer « by »")
        if not self._one("SELECT id FROM knowledge_entries WHERE id=?", (entry_id,)):
            raise KeyError(entry_id)
        self._exec("UPDATE knowledge_entries SET validation_status=?, validated_by=?, validated_at=?, updated_at=? "
                   "WHERE id=?", (status, by if status == "validated" else None,
                                  _now() if status == "validated" else None, _now(), entry_id))
        return self.get_entry(entry_id)

    # ------------------------------------------------------------------ errors (Learn)
    @staticmethod
    def _confidence(sig: dict) -> float:
        return round((sig["successes"] + 1) / (sig["successes"] + sig["failures"] + 2), 2)

    def _decorate(self, sig: dict) -> dict:
        sig["confidence"] = self._confidence(sig) if sig.get("solution") else None
        return sig

    def record_error(self, module: str, message: str, project_id: int | None = None) -> dict:
        key = signature_key(module, message)
        now = _now()
        existing = self._one("SELECT * FROM error_signatures WHERE key=?", (key,))
        if existing is None:
            self._exec("INSERT INTO error_signatures (key, module, normalized, example_message, first_seen, last_seen) "
                       "VALUES (?,?,?,?,?,?)", (key, module, normalize(message), message, now, now))
            existing = self._one("SELECT * FROM error_signatures WHERE key=?", (key,))
        seen_in_project = bool(project_id) and self._one(
            "SELECT id FROM error_occurrences WHERE signature_id=? AND project_id=?", (existing["id"], project_id))
        self._exec("INSERT INTO error_occurrences (signature_id, project_id, message, created_at) VALUES (?,?,?,?)",
                   (existing["id"], project_id, message, now))
        self._exec("UPDATE error_signatures SET occurrences=occurrences+1, last_seen=?, "
                   "project_count=project_count+? WHERE id=?",
                   (now, 1 if project_id and not seen_in_project else 0, existing["id"]))
        sig = self._decorate(self._one("SELECT * FROM error_signatures WHERE id=?", (existing["id"],)))
        sig["known"] = sig["solution"] is not None
        return sig

    def set_solution(self, signature_id: int, solution: str, root_cause: str | None = None,
                     status: str = "proposed") -> dict:
        if status not in STATUSES:
            raise ValidationError(f"status doit être parmi {sorted(STATUSES)}")
        if not self._one("SELECT id FROM error_signatures WHERE id=?", (signature_id,)):
            raise KeyError(signature_id)
        self._exec("UPDATE error_signatures SET solution=?, root_cause=?, solution_status=? WHERE id=?",
                   (solution, root_cause, status, signature_id))
        return self.get_signature(signature_id)

    def feedback(self, signature_id: int, useful: bool) -> dict:
        col = "successes" if useful else "failures"
        if not self._one("SELECT id FROM error_signatures WHERE id=?", (signature_id,)):
            raise KeyError(signature_id)
        self._exec(f"UPDATE error_signatures SET {col}={col}+1 WHERE id=?", (signature_id,))
        return self.get_signature(signature_id)

    def get_signature(self, signature_id: int) -> dict | None:
        s = self._one("SELECT * FROM error_signatures WHERE id=?", (signature_id,))
        return self._decorate(s) if s else None

    def list_signatures(self, module: str | None = None) -> list[dict]:
        rows = self._all("SELECT * FROM error_signatures" + (" WHERE module=?" if module else "") +
                         " ORDER BY occurrences DESC", (module,) if module else ())
        return [self._decorate(r) for r in rows]

"""Persistence of the migration state: environment, step runs, step validations, inventories."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..knowledge.store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_env (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    data TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS step_state (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo',
    validated_by TEXT, validated_at TEXT,
    checklist TEXT, note TEXT,
    custom_script TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, step_id)
);
CREATE TABLE IF NOT EXISTS step_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_by TEXT,
    started_at TEXT NOT NULL, ended_at TEXT,
    exit_code INTEGER,
    script TEXT,
    log TEXT NOT NULL DEFAULT '',
    error_signatures TEXT
);
CREATE TABLE IF NOT EXISTS project_data (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, kind)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MigrationState:
    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store.conn.executescript(SCHEMA)

    # -------------------------------------------------------------- environment
    def get_env(self, project_id: int) -> dict:
        r = self.store._one("SELECT data FROM project_env WHERE project_id=?", (project_id,))
        return json.loads(r["data"]) if r else {}

    def set_env(self, project_id: int, data: dict) -> None:
        self.store._exec("INSERT INTO project_env (project_id, data, updated_at) VALUES (?,?,?) "
                         "ON CONFLICT(project_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                         (project_id, json.dumps(data), now()))

    # -------------------------------------------------------------- generic project data
    def get_data(self, project_id: int, kind: str) -> dict | None:
        r = self.store._one("SELECT data, updated_at FROM project_data WHERE project_id=? AND kind=?",
                            (project_id, kind))
        if not r:
            return None
        d = json.loads(r["data"])
        if isinstance(d, dict):
            d.setdefault("updated_at", r["updated_at"])
        return d

    def set_data(self, project_id: int, kind: str, data: dict) -> None:
        self.store._exec("INSERT INTO project_data (project_id, kind, data, updated_at) VALUES (?,?,?,?) "
                         "ON CONFLICT(project_id, kind) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                         (project_id, kind, json.dumps(data), now()))

    # -------------------------------------------------------------- step state
    def steps(self, project_id: int) -> dict[str, dict]:
        rows = self.store._all("SELECT * FROM step_state WHERE project_id=?", (project_id,))
        out = {}
        for r in rows:
            r["checklist"] = json.loads(r["checklist"]) if r["checklist"] else {}
            out[r["step_id"]] = r
        return out

    def step(self, project_id: int, step_id: str) -> dict:
        return self.steps(project_id).get(step_id) or {"project_id": project_id, "step_id": step_id,
                                                         "status": "todo", "checklist": {}, "custom_script": None,
                                                         "validated_by": None, "validated_at": None, "note": None}

    def update_step(self, project_id: int, step_id: str, **changes) -> dict:
        cur = self.step(project_id, step_id)
        cur.update(changes)
        self.store._exec(
            "INSERT INTO step_state (project_id, step_id, status, validated_by, validated_at, checklist, note, "
            "custom_script, updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id, step_id) DO UPDATE SET "
            "status=excluded.status, validated_by=excluded.validated_by, validated_at=excluded.validated_at, "
            "checklist=excluded.checklist, note=excluded.note, custom_script=excluded.custom_script, "
            "updated_at=excluded.updated_at",
            (project_id, step_id, cur["status"], cur.get("validated_by"), cur.get("validated_at"),
             json.dumps(cur.get("checklist") or {}), cur.get("note"), cur.get("custom_script"), now()))
        return self.step(project_id, step_id)

    # -------------------------------------------------------------- runs
    def start_run(self, project_id: int, step_id: str, mode: str, by: str | None, script: str | None) -> int:
        cur = self.store._exec("INSERT INTO step_runs (project_id, step_id, mode, status, started_by, started_at, script) "
                               "VALUES (?,?,?,?,?,?,?)", (project_id, step_id, mode, "running", by, now(), script))
        return cur.lastrowid

    def append_log(self, run_id: int, text: str) -> None:
        self.store._exec("UPDATE step_runs SET log = log || ? WHERE id=?", (text, run_id))

    def end_run(self, run_id: int, status: str, exit_code: int | None, signatures: list[int] | None = None) -> None:
        self.store._exec("UPDATE step_runs SET status=?, exit_code=?, ended_at=?, error_signatures=? WHERE id=?",
                         (status, exit_code, now(), json.dumps(signatures or []), run_id))

    def run(self, run_id: int) -> dict | None:
        r = self.store._one("SELECT * FROM step_runs WHERE id=?", (run_id,))
        if r:
            r["error_signatures"] = json.loads(r["error_signatures"]) if r["error_signatures"] else []
        return r

    def runs(self, project_id: int, step_id: str | None = None, limit: int = 50) -> list[dict]:
        sql = ("SELECT id, step_id, mode, status, started_by, started_at, ended_at, exit_code FROM step_runs "
               "WHERE project_id=?" + (" AND step_id=?" if step_id else "") + " ORDER BY id DESC LIMIT ?")
        params = (project_id, step_id, limit) if step_id else (project_id, limit)
        return self.store._all(sql, params)

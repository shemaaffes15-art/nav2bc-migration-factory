"""REST API of NAV2BC Migration Factory."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..al_scan import compare_counts, coverage, scan_al_project
from ..knowledge.store import Store, ValidationError
from ..migration import environment
from ..migration.plan import build_plan, find_step
from ..migration.runner import Runner, ingest_export, powershell_exe
from ..migration.state import MigrationState
from ..reports.analyzer import analyze_export, summarize
from ..upgrade_path import UnsupportedVersion, compute_path

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _decode(raw: bytes) -> str:
    # NAV exports are usually ANSI (cp1252/cp850) — try UTF-8 first.
    for enc in ("utf-8-sig", "cp1252", "cp850"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1)
    client: str | None = None
    scenario: Literal["A", "B", "C"] = "B"
    source_version: str
    target_version: int = Field(ge=14, le=40)
    target_online: bool = True


class EntryIn(BaseModel):
    source_version: str | None = None
    target_version: str | None = None
    source_table: str | None = None
    source_field: str | None = None
    target_table: str | None = None
    target_field: str | None = None
    change_type: str
    nature: Literal["structure", "functional"] = "structure"
    description: str
    documentation_url: str | None = None
    evidence: Literal["documented", "id_identity", "hypothesis", "consultant"] | None = None
    required_action: str | None = None
    risk: Literal["low", "medium", "high"] = "medium"
    confidence: float | None = Field(default=None, ge=0, le=1)
    scope: Literal["microsoft", "version", "client", "project"] = "project"
    observations: str | None = None


class StatusIn(BaseModel):
    status: Literal["proposed", "reviewed", "validated", "rejected"]
    by: str | None = None


class ErrorIn(BaseModel):
    module: Literal["code", "reports", "data", "validation", "other"]
    message: str = Field(min_length=1)
    project_id: int | None = None


class SolutionIn(BaseModel):
    solution: str = Field(min_length=1)
    root_cause: str | None = None
    status: Literal["proposed", "reviewed", "validated", "rejected"] = "proposed"


class FeedbackIn(BaseModel):
    useful: bool


class RunIn(BaseModel):
    by: str | None = None
    simulate: bool = False
    confirm: bool = False


class ScriptIn(BaseModel):
    script: str | None = None


class CheckIn(BaseModel):
    item: str
    checked: bool
    by: str = Field(min_length=1)


class CompleteIn(BaseModel):
    by: str = Field(min_length=1)
    note: str | None = None


class FolderIn(BaseModel):
    folder: str | None = None


def store_path(db_path: str | None) -> str:
    return db_path or os.environ.get("NAV2BC_DB", "data/nav2bc.db")


def create_app(db_path: str | None = None) -> FastAPI:
    store = Store(store_path(db_path))
    app = FastAPI(title="NAV2BC Migration Factory", version=__version__)
    app.state.store = store
    state = MigrationState(store)
    local_dir = str(Path(store_path(db_path)).parent / "runs")
    runner = Runner(store, state, local_dir)
    app.state.migration = state

    def _project(project_id: int) -> dict:
        p = store.get_project(project_id)
        if not p:
            raise HTTPException(404, "Projet introuvable")
        return p

    async def _read_upload(file: UploadFile, max_mb: int = 50) -> str:
        raw = await file.read()
        if len(raw) > max_mb * 1024 * 1024:
            raise HTTPException(413, f"Fichier trop volumineux ({max_mb} Mo maximum)")
        return _decode(raw)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    # ------------------------------------------------------------- upgrade path
    @app.get("/api/upgrade-path")
    def upgrade_path(source: str, target: int, online: bool = True) -> dict:
        try:
            return compute_path(source, target, online)
        except UnsupportedVersion as exc:
            raise HTTPException(422, str(exc))

    # ------------------------------------------------------------- projects
    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectIn) -> dict:
        try:
            path = compute_path(body.source_version, body.target_version, body.target_online)
        except UnsupportedVersion as exc:
            raise HTTPException(422, str(exc))
        project = store.create_project(**body.model_dump())
        project["upgrade_path"] = path
        return project

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return store.list_projects()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: int) -> dict:
        p = _project(project_id)
        p["upgrade_path"] = compute_path(p["source_version"], p["target_version"], p["target_online"])
        p["report_summary"] = _summary(store.list_report_analyses(project_id))
        return p

    @app.put("/api/projects/{project_id}")
    def update_project(project_id: int, body: ProjectIn) -> dict:
        _project(project_id)
        try:
            compute_path(body.source_version, body.target_version, body.target_online)
        except UnsupportedVersion as exc:
            raise HTTPException(422, str(exc))
        d = body.model_dump()
        store._exec("UPDATE projects SET name=?, client=?, scenario=?, source_version=?, target_version=?, "
                    "target_online=? WHERE id=?", (d["name"], d["client"], d["scenario"], d["source_version"],
                                                   d["target_version"], int(d["target_online"]), project_id))
        return get_project(project_id)

    # ------------------------------------------------------------- report engine
    @app.post("/api/reports/analyze")
    async def analyze_without_project(file: UploadFile = File(...)) -> dict:
        analyses = analyze_export(await _read_upload(file))
        return {"summary": summarize(analyses), "reports": [a.to_dict() for a in analyses]}

    @app.post("/api/projects/{project_id}/reports")
    async def analyze_for_project(project_id: int, file: UploadFile = File(...)) -> dict:
        _project(project_id)
        analyses = analyze_export(await _read_upload(file))
        if not analyses:
            raise HTTPException(422, "Aucun objet Report trouvé dans le fichier (export TXT C/AL attendu).")
        for a in analyses:
            store.save_report_analysis(project_id, a.to_dict())
            for issue in a.issues:
                if issue.severity == "error":
                    store.record_error("reports", f"{issue.code}: {issue.message}", project_id)
        return {"summary": summarize(analyses), "imported": [a.report_id for a in analyses]}

    @app.get("/api/projects/{project_id}/reports")
    def list_reports(project_id: int) -> dict:
        _project(project_id)
        reports = store.list_report_analyses(project_id)
        return {"summary": _summary(reports), "reports": [_brief(r) for r in reports]}

    @app.get("/api/projects/{project_id}/reports/{report_id}")
    def get_report(project_id: int, report_id: int) -> dict:
        _project(project_id)
        r = store.get_report_analysis(project_id, report_id)
        if not r:
            raise HTTPException(404, "Rapport introuvable dans ce projet")
        return r

    # ------------------------------------------------------------- knowledge base
    @app.get("/api/knowledge/entries")
    def list_entries(scope: str | None = None, validation_status: str | None = None, evidence: str | None = None,
                     q: str | None = None) -> list[dict]:
        return store.list_entries(scope=scope, validation_status=validation_status, evidence=evidence, q=q)

    @app.post("/api/knowledge/entries", status_code=201)
    def add_entry(body: EntryIn) -> dict:
        try:
            return store.add_entry(body.model_dump())
        except ValidationError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/api/knowledge/entries/{entry_id}")
    def get_entry(entry_id: int) -> dict:
        e = store.get_entry(entry_id)
        if not e:
            raise HTTPException(404, "Entrée introuvable")
        return e

    @app.patch("/api/knowledge/entries/{entry_id}")
    def update_entry(entry_id: int, body: dict) -> dict:
        try:
            return store.update_entry(entry_id, body)
        except KeyError:
            raise HTTPException(404, "Entrée introuvable")
        except ValidationError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/knowledge/entries/{entry_id}/status")
    def set_status(entry_id: int, body: StatusIn) -> dict:
        try:
            return store.set_entry_status(entry_id, body.status, body.by)
        except KeyError:
            raise HTTPException(404, "Entrée introuvable")
        except ValidationError as exc:
            raise HTTPException(422, str(exc))

    # ------------------------------------------------------------- errors (Learn loop)
    @app.post("/api/errors")
    def record_error(body: ErrorIn) -> dict:
        return store.record_error(body.module, body.message, body.project_id)

    @app.get("/api/errors")
    def list_errors(module: str | None = None) -> list[dict]:
        return store.list_signatures(module)

    @app.post("/api/errors/{signature_id}/solution")
    def set_solution(signature_id: int, body: SolutionIn) -> dict:
        try:
            return store.set_solution(signature_id, body.solution, body.root_cause, body.status)
        except KeyError:
            raise HTTPException(404, "Signature introuvable")

    @app.post("/api/errors/{signature_id}/feedback")
    def feedback(signature_id: int, body: FeedbackIn) -> dict:
        try:
            return store.feedback(signature_id, body.useful)
        except KeyError:
            raise HTTPException(404, "Signature introuvable")

    # ------------------------------------------------------------- system / dashboard
    @app.get("/api/system")
    def system() -> dict:
        return {"version": __version__, "os": os.name, "powershell": powershell_exe(),
                "execution_available": bool(powershell_exe())}

    def _env(p: dict) -> dict:
        return environment.merge(p, state.get_env(p["id"]))

    def _plan(p: dict) -> dict:
        env = _env(p)
        plan = build_plan(p, env)
        states = state.steps(p["id"])
        done = total = 0
        for ph in plan["phases"]:
            ph_done = 0
            for s in ph["steps"]:
                st = states.get(s["id"], {})
                s["status"] = st.get("status", "todo")
                if s["status"] == "running" and not runner.is_running(p["id"], s["id"]):
                    s["status"] = "todo"
                s["validated_by"] = st.get("validated_by")
                s["validated_at"] = st.get("validated_at")
                s["missing"] = environment.missing(env, s["requires"])
                s.pop("script", None)
                total += 1
                if s["status"] == "done":
                    done += 1
                    ph_done += 1
            ph["done"] = ph_done
        plan["progress"] = {"done": done, "total": total}
        return plan

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        out = []
        for p in store.list_projects():
            plan = _plan(p)
            inv = state.get_data(p["id"], "inventory")
            errors = store._one("SELECT COUNT(DISTINCT signature_id) AS n FROM error_occurrences WHERE project_id=?",
                                (p["id"],))["n"]
            current = next((s for ph in plan["phases"] for s in ph["steps"] if s["status"] != "done"), None)
            out.append({**p, "progress": plan["progress"], "object_count": inv["object_count"] if inv else None,
                        "error_count": errors, "current_step": current and {"id": current["id"],
                                                                            "title": current["title"],
                                                                            "phase": current["phase_label"]},
                        "path": plan["upgrade_path"]["summary"]})
        known = store._one("SELECT COUNT(*) AS n FROM error_signatures WHERE solution IS NOT NULL")["n"]
        return {"projects": out, "known_solutions": known,
                "knowledge_entries": store._one("SELECT COUNT(*) AS n FROM knowledge_entries")["n"]}

    # ------------------------------------------------------------- environment
    @app.get("/api/projects/{project_id}/environment")
    def get_environment(project_id: int) -> dict:
        p = _project(project_id)
        return {"fields": environment.describe(p), "values": _env(p)}

    @app.put("/api/projects/{project_id}/environment")
    def put_environment(project_id: int, body: dict) -> dict:
        p = _project(project_id)
        keys = {f[0] for f in environment.FIELDS}
        state.set_env(project_id, {k: str(v) for k, v in body.items() if k in keys})
        return {"fields": environment.describe(p), "values": _env(p)}

    # ------------------------------------------------------------- plan / steps
    @app.get("/api/projects/{project_id}/plan")
    def get_plan(project_id: int) -> dict:
        return _plan(_project(project_id))

    def _step(p: dict, step_id: str) -> tuple[dict, dict, dict]:
        env = _env(p)
        plan = build_plan(p, env)
        s = find_step(plan, step_id)
        if not s:
            raise HTTPException(404, "Étape introuvable")
        return s, env, plan

    @app.get("/api/projects/{project_id}/steps/{step_id}")
    def get_step(project_id: int, step_id: str) -> dict:
        p = _project(project_id)
        s, env, _ = _step(p, step_id)
        st = state.step(project_id, step_id)
        script, origin = runner.script_for(p, env, s)
        running = runner.is_running(project_id, step_id)
        return {**s, "state": {**st, "status": st["status"] if st["status"] != "running" or running else "todo"},
                "script": script, "generated_script": s.get("script"), "script_origin": origin,
                "missing": environment.missing(env, s["requires"]), "runs": state.runs(project_id, step_id, 10),
                "running_run": running}

    @app.put("/api/projects/{project_id}/steps/{step_id}/script")
    def put_script(project_id: int, step_id: str, body: ScriptIn) -> dict:
        p = _project(project_id)
        _step(p, step_id)
        state.update_step(project_id, step_id, custom_script=(body.script or None))
        return get_step(project_id, step_id)

    @app.post("/api/projects/{project_id}/steps/{step_id}/run")
    def run_step(project_id: int, step_id: str, body: RunIn) -> dict:
        p = _project(project_id)
        s, env, plan = _step(p, step_id)
        if s["kind"] == "powershell" and not body.simulate:
            miss = environment.missing(env, s["requires"])
            if miss:
                raise HTTPException(422, "Environnement incomplet : " + ", ".join(miss))
            if s["destructive"] and not body.confirm:
                raise HTTPException(422, "Étape qui modifie une base ou un service : confirmation requise.")
        try:
            run_id = runner.start(p, env, plan, step_id, body.by, body.simulate)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc))
        if not body.simulate and s["kind"] == "powershell":
            state.update_step(project_id, step_id, validated_by=body.by, validated_at=now_iso())
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: int, offset: int = 0) -> dict:
        r = state.run(run_id)
        if not r:
            raise HTTPException(404, "Exécution introuvable")
        log = r["log"]
        r["log"] = log[offset:]
        r["log_length"] = len(log)
        if r["error_signatures"]:
            r["errors"] = [store.get_signature(i) for i in r["error_signatures"]]
        return r

    @app.post("/api/projects/{project_id}/steps/{step_id}/checklist")
    def check_item(project_id: int, step_id: str, body: CheckIn) -> dict:
        p = _project(project_id)
        _step(p, step_id)
        st = state.step(project_id, step_id)
        cl = dict(st.get("checklist") or {})
        if body.checked:
            cl[body.item] = {"by": body.by, "at": now_iso()}
        else:
            cl.pop(body.item, None)
        state.update_step(project_id, step_id, checklist=cl)
        return get_step(project_id, step_id)

    @app.post("/api/projects/{project_id}/steps/{step_id}/complete")
    def complete_step(project_id: int, step_id: str, body: CompleteIn) -> dict:
        p = _project(project_id)
        s, _, _ = _step(p, step_id)
        st = state.step(project_id, step_id)
        unchecked = [i for i in s["checklist"] if i not in (st.get("checklist") or {})]
        if unchecked:
            raise HTTPException(422, "Points de contrôle non cochés : " + " ; ".join(unchecked))
        state.update_step(project_id, step_id, status="done", validated_by=body.by, validated_at=now_iso(),
                          note=body.note)
        return get_step(project_id, step_id)

    @app.post("/api/projects/{project_id}/steps/{step_id}/reset")
    def reset_step(project_id: int, step_id: str) -> dict:
        p = _project(project_id)
        _step(p, step_id)
        state.update_step(project_id, step_id, status="todo", validated_by=None, validated_at=None)
        return get_step(project_id, step_id)

    # ------------------------------------------------------------- inventory / data
    @app.post("/api/projects/{project_id}/inventory")
    async def upload_inventory(project_id: int, file: UploadFile = File(...)) -> dict:
        _project(project_id)
        text = await _read_upload(file, 300)
        res = ingest_export(store, state, project_id, text, file.filename or "import")
        if not res["object_count"]:
            raise HTTPException(422, "Aucun objet trouvé : un export TXT C/AL est attendu.")
        state.update_step(project_id, "prep.inventory", status="done")
        return res

    @app.get("/api/projects/{project_id}/inventory")
    def get_inventory(project_id: int, with_objects: bool = False) -> dict:
        _project(project_id)
        inv = state.get_data(project_id, "inventory")
        if not inv:
            return {"empty": True}
        if not with_objects:
            inv = {k: v for k, v in inv.items() if k != "objects"}
        return inv

    @app.post("/api/projects/{project_id}/al-scan")
    def al_scan(project_id: int, body: FolderIn) -> dict:
        p = _project(project_id)
        inv = state.get_data(project_id, "inventory")
        if not inv:
            raise HTTPException(422, "Importez d'abord l'inventaire (export TXT).")
        folder = body.folder or _env(p)["al_project"]
        try:
            scan = scan_al_project(folder)
        except FileNotFoundError as exc:
            raise HTTPException(422, str(exc))
        cov = coverage(inv, scan)
        state.set_data(project_id, "al_scan", scan)
        state.set_data(project_id, "coverage", cov)
        return {"scan": {k: scan[k] for k in ("folder", "al_file_count")}, "coverage": cov}

    @app.get("/api/projects/{project_id}/data")
    def get_data(project_id: int) -> dict:
        _project(project_id)
        inv = state.get_data(project_id, "inventory") or {}
        scan = state.get_data(project_id, "al_scan")
        return {"custom_tables": inv.get("custom_tables", []), "custom_fields": inv.get("custom_fields", []),
                "coverage": state.get_data(project_id, "coverage"),
                "al_scan": scan and {k: scan[k] for k in ("folder", "al_file_count", "updated_at") if k in scan},
                "rowcount": state.get_data(project_id, "rowcount")}

    @app.post("/api/projects/{project_id}/rowcount")
    async def upload_rowcount(project_id: int, source: UploadFile = File(...), target: UploadFile = File(...)) -> dict:
        import tempfile
        _project(project_id)
        with tempfile.TemporaryDirectory() as d:
            sp, tp = Path(d) / "s.csv", Path(d) / "t.csv"
            sp.write_bytes(await source.read())
            tp.write_bytes(await target.read())
            try:
                res = compare_counts(str(sp), str(tp))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(422, f"CSV illisible : {exc}")
        state.set_data(project_id, "rowcount", res)
        return res

    # ------------------------------------------------------------- UI
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _brief(r: dict) -> dict:
    return {
        "report_id": r["report_id"], "name": r["name"], "origin": r["origin"], "layouts": r["layouts"],
        "complexity": {k: r["complexity"][k] for k in ("score", "class", "label")},
        "strategy": r["strategy"],
        "errors": sum(1 for i in r["issues"] if i["severity"] == "error"),
        "warnings": sum(1 for i in r["issues"] if i["severity"] == "warning"),
        "analyzed_at": r.get("analyzed_at"),
    }


def _summary(reports: list[dict]) -> dict:
    by_class: dict[str, int] = {}
    by_layout: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    for r in reports:
        lbl = r["complexity"]["label"]
        by_class[lbl] = by_class.get(lbl, 0) + 1
        for layout in r["layouts"] or ["Aucun"]:
            by_layout[layout] = by_layout.get(layout, 0) + 1
        s = r["strategy"]["label"]
        by_strategy[s] = by_strategy.get(s, 0) + 1
    return {"report_count": len(reports), "by_complexity": by_class, "by_layout": by_layout,
            "by_strategy": by_strategy}

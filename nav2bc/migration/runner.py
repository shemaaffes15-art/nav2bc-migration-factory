"""Step execution.

PowerShell steps run on the machine that hosts the application (Windows with the NAV / BC tools).
On another system, or when the consultant asks for it, the step runs in *simulation*: the script
is generated and logged, nothing is executed. Errors found in the output are recorded in the
error journal; a signature that already has a solution is shown right away.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import traceback
from pathlib import Path

from ..al_scan import compare_counts, coverage, extract_errors, scan_al_project
from ..inventory import build_inventory
from ..knowledge.store import Store
from ..reports.analyzer import analyze_export
from .plan import find_step
from .state import MigrationState, now

MODULE_BY_PHASE = {"al": "code", "validation": "validation", "prep": "other", "cloud": "data"}


def powershell_exe() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell") or shutil.which("pwsh")


def ingest_export(store: Store, state: MigrationState, project_id: int, text: str, source: str) -> dict:
    """Inventory + report analysis of a full C/AL export."""
    inv = build_inventory(text)
    inv["source"] = source
    state.set_data(project_id, "inventory", inv)
    analyses = analyze_export(text)
    for a in analyses:
        store.save_report_analysis(project_id, a.to_dict())
        for issue in a.issues:
            if issue.severity == "error":
                store.record_error("reports", f"{issue.code}: {issue.message}", project_id)
    return {"object_count": inv["object_count"], "report_count": len(analyses),
            "custom_tables": len(inv["custom_tables"]), "custom_fields": len(inv["custom_fields"]),
            "risks": len(inv["risks"])}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "cp850"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


class Runner:
    def __init__(self, store: Store, state: MigrationState, local_dir: str):
        self.store, self.state = store, state
        self.local_dir = Path(local_dir)
        self._active: dict[tuple[int, str], int] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- scripts
    def script_for(self, project: dict, env: dict, step: dict) -> tuple[str | None, str]:
        st = self.state.step(project["id"], step["id"])
        if st.get("custom_script"):
            return st["custom_script"], "modifié dans l'application"
        override = Path(env["workdir"]) / "scripts" / f"{step['id']}.ps1"
        try:
            if override.exists():
                return _read_text(override), f"fichier {override}"
        except OSError:
            pass
        return step.get("script"), "généré"

    def is_running(self, project_id: int, step_id: str) -> int | None:
        with self._lock:
            return self._active.get((project_id, step_id))

    # ---------------------------------------------------------------- start
    def start(self, project: dict, env: dict, plan: dict, step_id: str, by: str | None, simulate: bool) -> int:
        step = find_step(plan, step_id)
        if not step:
            raise KeyError(step_id)
        if step["kind"] == "manual":
            raise ValueError("Étape manuelle : cochez la liste de contrôle et validez-la.")
        if self.is_running(project["id"], step_id):
            raise ValueError("Cette étape est déjà en cours.")
        script, origin = self.script_for(project, env, step)
        mode = "internal" if step["kind"] == "internal" else ("simulation" if simulate or not powershell_exe() else "execution")
        if mode == "execution" and not (by or "").strip():
            raise ValueError("Exécution nominative : indiquez votre nom.")
        run_id = self.state.start_run(project["id"], step_id, mode, by, script)
        self.state.append_log(run_id, f"[{now()}] {step['title']} — mode {mode}" +
                              (f", script {origin}" if script else "") + "\n")
        self.state.update_step(project["id"], step_id, status="running")
        with self._lock:
            self._active[(project["id"], step_id)] = run_id
        t = threading.Thread(target=self._run, args=(project, env, step, run_id, mode, script), daemon=True)
        t.start()
        return run_id

    def _finish(self, project: dict, step: dict, run_id: int, ok: bool, code: int | None, log: str,
                mode: str) -> None:
        sigs: list[int] = []
        if not ok or mode == "execution":
            module = MODULE_BY_PHASE.get(step["phase"], "data")
            for msg in extract_errors(log) if not ok or "error AL" in log else []:
                sig = self.store.record_error(module, msg, project["id"])
                sigs.append(sig["id"])
                if sig.get("known"):
                    self.state.append_log(run_id, f"\n>>> Erreur déjà connue (vue {sig['occurrences']} fois) — "
                                                  f"solution : {sig['solution']}\n")
        status = "simulated" if mode == "simulation" else ("success" if ok else "failed")
        self.state.end_run(run_id, status, code, sigs)
        new_status = {"success": "done", "failed": "failed"}.get(status)
        current = self.state.step(project["id"], step["id"])
        self.state.update_step(project["id"], step["id"],
                               status=new_status or (current["status"] if current["status"] != "running" else "todo"))
        with self._lock:
            self._active.pop((project["id"], step["id"]), None)

    def _run(self, project: dict, env: dict, step: dict, run_id: int, mode: str, script: str | None) -> None:
        log_parts: list[str] = []

        def log(text: str) -> None:
            log_parts.append(text)
            self.state.append_log(run_id, text)

        try:
            if step["kind"] == "internal":
                ok = self._internal(project, env, step, log)
                self._finish(project, step, run_id, ok, 0 if ok else 1, "".join(log_parts), mode)
                return
            if mode == "simulation":
                reason = "demandée" if powershell_exe() else "PowerShell indisponible sur cette machine"
                log(f"Simulation ({reason}) : le script n'est pas exécuté.\n\n{script}\n")
                self._finish(project, step, run_id, True, None, "".join(log_parts), mode)
                return
            ok, code = self._powershell(project, env, step, run_id, script or "", log)
            if ok:
                self._after(project, env, step, log)
            self._finish(project, step, run_id, ok, code, "".join(log_parts), mode)
        except Exception as exc:  # noqa: BLE001 — everything goes to the step log
            log(f"\nErreur interne : {exc}\n{traceback.format_exc()}\n")
            self._finish(project, step, run_id, False, None, "".join(log_parts), mode)

    def _powershell(self, project: dict, env: dict, step: dict, run_id: int, script: str, log) -> tuple[bool, int]:
        folder = Path(env["workdir"]) / "scripts" / "runs"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = self.local_dir / f"project_{project['id']}" / "runs"
            folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{step['id']}_{run_id}.ps1"
        path.write_text(script, encoding="utf-8-sig")
        log(f"Script : {path}\n\n")
        proc = subprocess.Popen([powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=str(folder), text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert proc.stdout is not None
        for line in proc.stdout:
            log(line)
        code = proc.wait()
        log(f"\n[{now()}] Code de sortie : {code}\n")
        return code == 0, code

    # ---------------------------------------------------------------- hooks
    def _after(self, project: dict, env: dict, step: dict, log) -> None:
        if step["id"] == "prep.export":
            f = Path(env["workdir"]) / "export" / "nav_all_objects.txt"
            if f.exists():
                res = ingest_export(self.store, self.state, project["id"], _read_text(f), str(f))
                log(f"Inventaire mis à jour : {res['object_count']} objets, {res['report_count']} rapports analysés.\n")
                self.state.update_step(project["id"], "prep.inventory", status="done")

    def _internal(self, project: dict, env: dict, step: dict, log) -> bool:
        pid = project["id"]
        sid = step["id"]
        if sid == "prep.inventory":
            f = Path(env["workdir"]) / "export" / "nav_all_objects.txt"
            if f.exists():
                res = ingest_export(self.store, self.state, pid, _read_text(f), str(f))
                log(f"{res['object_count']} objets inventoriés, {res['report_count']} rapports analysés, "
                    f"{res['custom_tables']} tables client, {res['custom_fields']} champs ajoutés au standard.\n")
                return True
            inv = self.state.get_data(pid, "inventory")
            if inv:
                log(f"Inventaire déjà importé ({inv['object_count']} objets, source : {inv.get('source')}).\n")
                return True
            log(f"Aucun export trouvé ({f}). Lancez l'étape d'export ou importez le fichier TXT depuis l'écran "
                "Inventaire.\n")
            return False
        if sid == "al.coverage":
            inv = self.state.get_data(pid, "inventory")
            if not inv:
                log("Inventaire absent : importez d'abord l'export TXT.\n")
                return False
            scan = scan_al_project(env["al_project"])
            cov = coverage(inv, scan)
            self.state.set_data(pid, "al_scan", scan)
            self.state.set_data(pid, "coverage", cov)
            log(f"{scan['al_file_count']} fichiers AL lus. Tables client absentes de l'AL : {cov['missing_tables']}. "
                f"Champs ajoutés au standard absents de l'AL : {cov['missing_fields']}.\n")
            return cov["missing_tables"] == 0 and cov["missing_fields"] == 0
        if sid == "validation.compare":
            d = Path(env["workdir"]) / "validation"
            src, tgt = d / "rowcount_source.csv", d / "rowcount_target.csv"
            if not (src.exists() and tgt.exists()):
                log(f"Fichiers de comptage absents dans {d}. Lancez les deux étapes de comptage.\n")
                return False
            res = compare_counts(str(src), str(tgt))
            self.state.set_data(pid, "rowcount", res)
            log(f"{res['ok']} tables identiques, {res['diff']} écarts, {res['missing_target']} absentes de la cible.\n")
            return res["diff"] == 0 and res["missing_target"] == 0
        if sid == "cloud.readiness":
            inv = self.state.get_data(pid, "inventory") or {}
            cov = self.state.get_data(pid, "coverage")
            high = [r for r in inv.get("risks", []) if r["severity"] == "high"]
            log(f"Fonctions bloquantes en SaaS : {sum(r['count'] for r in high)} objet(s) "
                f"({', '.join(r['id'] for r in high) or 'aucune'}).\n")
            if cov is None:
                log("Couverture AL non contrôlée : lancez l'étape « Contrôler la couverture des données ».\n")
            else:
                log(f"Tables client absentes de l'AL : {cov['missing_tables']} ; champs : {cov['missing_fields']}.\n")
            states = self.state.steps(pid)
            pending = [s for s in states.values() if s["status"] in ("failed", "running")]
            log(f"Étapes en échec ou en cours : {len(pending)}.\n")
            return cov is not None and cov["missing_tables"] == 0 and cov["missing_fields"] == 0 and not pending
        log("Étape interne inconnue.\n")
        return False

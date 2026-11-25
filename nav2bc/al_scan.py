"""Read an AL project and compare it with the inventory (data coverage for the cloud migration),
and read compiler / tool logs to feed the error-learning loop."""
from __future__ import annotations

import csv
import re
from pathlib import Path

TABLE_RE = re.compile(r'^\s*table\s+(\d+)\s+("?)([^"\n{]+?)\2\s*(?:\{|$)', re.IGNORECASE | re.MULTILINE)
TABLEEXT_RE = re.compile(r'^\s*tableextension\s+(\d+)\s+("?)([^"\n]+?)\2\s+extends\s+("?)([^"\n{]+?)\4\s*(?:\{|$)',
                         re.IGNORECASE | re.MULTILINE)
FIELD_RE = re.compile(r'^\s*field\s*\(\s*(\d+)\s*;\s*("?)([^";]+)\2\s*;', re.IGNORECASE | re.MULTILINE)
AL_ERROR_RE = re.compile(r"(error|warning)\s+(AL\d{4})\s*:\s*(.+)$", re.IGNORECASE)
PS_ERROR_RE = re.compile(r"^(?:.*?\b(?:Exception|Error|Erreur)\b.*)$", re.IGNORECASE)


def scan_al_project(folder: str) -> dict:
    root = Path(folder)
    if not root.exists():
        raise FileNotFoundError(f"Dossier AL introuvable : {folder}")
    tables, extensions = {}, []
    for f in root.rglob("*.al"):
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for m in TABLE_RE.finditer(text):
            body = text[m.end():]
            nxt = re.search(r"^\s*(table|tableextension|page|codeunit|report)\s+\d+", body, re.IGNORECASE | re.MULTILINE)
            body = body[: nxt.start()] if nxt else body
            tables[int(m.group(1))] = {"id": int(m.group(1)), "name": m.group(3).strip(),
                                       "fields": [int(x.group(1)) for x in FIELD_RE.finditer(body)], "file": f.name}
        for m in TABLEEXT_RE.finditer(text):
            body = text[m.end():]
            nxt = re.search(r"^\s*(table|tableextension|page|codeunit|report)\s+\d+", body, re.IGNORECASE | re.MULTILINE)
            body = body[: nxt.start()] if nxt else body
            extensions.append({"id": int(m.group(1)), "name": m.group(3).strip(), "extends": m.group(5).strip(),
                               "fields": [int(x.group(1)) for x in FIELD_RE.finditer(body)], "file": f.name})
    return {"folder": str(root), "tables": list(tables.values()), "tableextensions": extensions,
            "al_file_count": sum(1 for _ in root.rglob("*.al"))}


def coverage(inventory: dict, scan: dict) -> dict:
    al_tables = {t["id"]: t for t in scan["tables"]}
    ext_fields: dict[str, set[int]] = {}
    for e in scan["tableextensions"]:
        ext_fields.setdefault(e["extends"].lower(), set()).update(e["fields"])
    tables = []
    for t in inventory.get("custom_tables", []):
        found = al_tables.get(t["id"])
        tables.append({**t, "in_al": bool(found), "al_field_count": len(found["fields"]) if found else 0})
    fields = []
    for f in inventory.get("custom_fields", []):
        fields.append({**f, "in_al": f["id"] in ext_fields.get(f["table"].lower(), set())})
    return {
        "tables": tables, "fields": fields,
        "missing_tables": sum(1 for t in tables if not t["in_al"]),
        "missing_fields": sum(1 for f in fields if not f["in_al"]),
    }


def extract_errors(log: str, limit: int = 50) -> list[str]:
    """Error lines worth a signature: AL diagnostics first, otherwise PowerShell / tool errors."""
    out = []
    for line in log.splitlines():
        line = line.strip()
        m = AL_ERROR_RE.search(line)
        if m and m.group(1).lower() == "error":
            out.append(f"{m.group(2)}: {m.group(3).strip()}")
        elif not m and PS_ERROR_RE.match(line) and len(line) > 12:
            out.append(line[:400])
        if len(out) >= limit:
            break
    seen, uniq = set(), []
    for e in out:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


GUID_SUFFIX = re.compile(r"\$[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _read_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = GUID_SUFFIX.sub("", row.get("TableName", "")).strip()
            try:
                n = int(float(row.get("Rows") or 0))
            except ValueError:
                n = 0
            counts[name] = counts.get(name, 0) + n
    return counts


def compare_counts(source_csv: str, target_csv: str) -> dict:
    src, tgt = _read_counts(Path(source_csv)), _read_counts(Path(target_csv))
    rows = []
    for name in sorted(set(src) | set(tgt)):
        if "$" not in name:  # system tables without company prefix
            continue
        s, t = src.get(name), tgt.get(name)
        status = "ok" if s == t else ("missing_target" if t is None else ("new_target" if s is None else "diff"))
        if s == 0 and t is None:
            status = "ok"
        rows.append({"table": name, "source": s, "target": t, "status": status,
                     "delta": (t or 0) - (s or 0)})
    return {
        "rows": rows,
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "diff": sum(1 for r in rows if r["status"] == "diff"),
        "missing_target": sum(1 for r in rows if r["status"] == "missing_target"),
        "new_target": sum(1 for r in rows if r["status"] == "new_target"),
    }

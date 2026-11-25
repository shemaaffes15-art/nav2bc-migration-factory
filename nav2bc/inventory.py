"""Inventory of a full C/AL text export (Discover).

Gives the consultant the size and shape of the database before planning: objects per type,
client / standard / add-on objects, client tables, fields added to standard tables (they must
exist in an AL extension, otherwise the cloud migration skips them), and functions that are not
available in Business Central Online.
"""
from __future__ import annotations

import re
from collections import Counter

from .cal.parser import parse_properties, split_objects
from .reports.analyzer import DETECTION_RULES, Settings, classify_origin

FIELD_RE = re.compile(r"^    \{ ?(\d+)\s*;\s*\w*\s*;([^;]+?)\s*;(\w+(?:\[\d+\])?)")
VAR_TYPE_RE = re.compile(r"@\d+\s*:\s*(?:TEMPORARY\s+)?([A-Za-z]+)")


def _fields(lines: list[str]) -> list[dict]:
    out = []
    for ln in lines:
        m = FIELD_RE.match(ln)
        if m:
            out.append({"id": int(m.group(1)), "name": m.group(2).strip(), "type": m.group(3)})
    return out


def build_inventory(text: str, settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    lo, hi = settings.custom_range
    objects = split_objects(text)
    by_type: Counter = Counter()
    by_origin: Counter = Counter()
    version_tags: Counter = Counter()
    rows: list[dict] = []
    custom_tables: list[dict] = []
    custom_fields: list[dict] = []
    risks: dict[str, dict] = {}

    for o in objects:
        meta = parse_properties(o.section("OBJECT-PROPERTIES"))
        version_list = meta.get("Version List", "")
        modified = meta.get("Modified", "No") == "Yes"
        origin = classify_origin(o.object_id, version_list, modified, settings)
        by_type[o.object_type] += 1
        by_origin[origin] += 1
        for tag in [t.strip() for t in version_list.split(",") if t.strip()]:
            version_tags[re.sub(r"[\d.]+$", "", tag) or tag] += 1
        rows.append({"type": o.object_type, "id": o.object_id, "name": o.name, "version_list": version_list,
                     "modified": modified, "origin": origin, "date": meta.get("Date", "")})

        if o.object_type == "Table":
            fields = _fields(o.section("FIELDS"))
            if lo <= o.object_id <= hi:
                custom_tables.append({"id": o.object_id, "name": o.name, "field_count": len(fields)})
            else:
                for f in fields:
                    if lo <= f["id"] <= hi:
                        custom_fields.append({"table_id": o.object_id, "table": o.name, **f})

        code = "\n".join(o.section("CODE")) + "\n" + "\n".join(
            "\n".join(o.section(s)) for s in ("PROPERTIES", "FIELDS", "DATASET", "CONTROLS", "DATAITEMS"))
        var_types = VAR_TYPE_RE.findall("\n".join(o.section("CODE")))
        for rule in DETECTION_RULES:
            rx = re.compile(rule["pattern"], re.IGNORECASE)
            hit = any(rx.search(t) for t in var_types) if rule["target"] == "var_type" else bool(rx.search(code))
            if hit:
                r = risks.setdefault(rule["id"], {k: rule[k] for k in ("id", "category", "severity", "message",
                                                                        "action", "validation_status")} | {"objects": []})
                if len(r["objects"]) < 200:
                    r["objects"].append(f"{o.object_type} {o.object_id} {o.name}")
                r["count"] = r.get("count", 0) + 1

    return {
        "object_count": len(objects),
        "by_type": dict(by_type.most_common()),
        "by_origin": dict(by_origin),
        "version_tags": dict(version_tags.most_common(30)),
        "modified_count": sum(1 for r in rows if r["modified"]),
        "custom_tables": custom_tables,
        "custom_fields": custom_fields,
        "risks": sorted(risks.values(), key=lambda r: ({"high": 0, "medium": 1, "low": 2}[r["severity"]], -r["count"])),
        "objects": rows,
    }

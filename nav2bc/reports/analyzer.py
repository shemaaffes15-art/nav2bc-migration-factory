"""Report Object Engine — inventory, structure analysis, complexity and strategy.

Input: C/AL text export (one or many objects). Output: one JSON-serialisable analysis per report.
Everything here is deterministic; strategies are *proposals* that the consultant validates.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from ..cal.parser import CalObject, parse_code, parse_dataset, parse_properties, split_objects
from .rdlc import analyze_rdlc
from .word import analyze_word_layout

TRIGGER_NAMES = (
    "OnInitReport", "OnPreReport", "OnPostReport", "OnPreDataItem", "OnAfterGetRecord",
    "OnPostDataItem", "OnOpenPage", "OnClosePage", "OnQueryClosePage",
)
INTEGER_TABLE_ID = 2000000026
TABLE_RE = re.compile(r"Table(\d+)")
TYPED_REF_RE = re.compile(r"\b(Record|Codeunit|Report|Page|XMLport|Query)\s+(\d+)\b", re.IGNORECASE)
RUN_REF_RE = re.compile(r"\b(CODEUNIT|REPORT|PAGE|XMLPORT)\.RUN(?:MODAL)?\s*\(\s*(\d+)", re.IGNORECASE)
CLASSIC_CONTROL_RE = re.compile(r"^\s+\{ ?\d+\s*;(Label|TextBox|Shape|Image|PictureBox|CheckBox|Frame)\b")

# Initial calibration of the complexity score. Thresholds are deliberately simple and will be
# recalibrated with the real effort recorded on past projects.
COMPLEXITY_CLASSES = [(15, "simple", "Simple"), (30, "medium", "Moyen"), (50, "complex", "Complexe")]
HIGHLY_COMPLEX = ("highly_complex", "Très complexe")


@dataclass
class Settings:
    custom_range: tuple[int, int] = (50000, 99999)


def _load_detection_rules() -> list[dict[str, Any]]:
    data = resources.files("nav2bc.data").joinpath("detection_rules.json").read_text(encoding="utf-8")
    return json.loads(data)["rules"]


DETECTION_RULES = _load_detection_rules()


def _code_lines(value: str) -> int:
    lines = [ln.strip() for ln in value.splitlines()]
    body = [ln for ln in lines if ln and ln.upper() not in ("BEGIN", "END", "END;", "VAR")]
    return len(body)


def _is_trigger_value(value: str) -> bool:
    head = value.lstrip().upper()
    return head.startswith("BEGIN") or head.startswith("VAR")


@dataclass
class Issue:
    severity: str  # error | warning | info
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass
class Factor:
    label: str
    points: int
    evidence: str


@dataclass
class ReportAnalysis:
    report_id: int
    name: str
    version_list: str = ""
    modified: bool = False
    origin: str = "custom"
    layouts: list[str] = field(default_factory=list)
    default_layout: str | None = None
    processing_only: bool = False
    dataitems: list[dict] = field(default_factory=list)
    columns: list[dict] = field(default_factory=list)
    triggers: list[dict] = field(default_factory=list)
    globals: list[dict] = field(default_factory=list)
    procedures: list[dict] = field(default_factory=list)
    temporary_tables: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    request_page_fields: int = 0
    dependencies: dict[str, list[int]] = field(default_factory=dict)
    risks: list[dict] = field(default_factory=list)
    classic: dict | None = None
    rdlc: dict | None = None
    word: dict | None = None
    issues: list[Issue] = field(default_factory=list)
    factors: list[Factor] = field(default_factory=list)
    complexity_score: int = 0
    complexity_class: str = "simple"
    complexity_label: str = "Simple"
    strategy: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "name": self.name,
            "version_list": self.version_list,
            "modified": self.modified,
            "origin": self.origin,
            "layouts": self.layouts,
            "default_layout": self.default_layout,
            "processing_only": self.processing_only,
            "dataitems": self.dataitems,
            "columns": self.columns,
            "triggers": self.triggers,
            "globals": self.globals,
            "procedures": self.procedures,
            "temporary_tables": self.temporary_tables,
            "labels": self.labels,
            "request_page_fields": self.request_page_fields,
            "dependencies": self.dependencies,
            "risks": self.risks,
            "classic": self.classic,
            "rdlc": self.rdlc,
            "word": self.word,
            "issues": [i.to_dict() for i in self.issues],
            "complexity": {
                "score": self.complexity_score,
                "class": self.complexity_class,
                "label": self.complexity_label,
                "factors": [f.__dict__ for f in self.factors],
            },
            "strategy": self.strategy,
        }


# --------------------------------------------------------------------------------------------
# Origin and strategy
# --------------------------------------------------------------------------------------------

def classify_origin(report_id: int, version_list: str, modified: bool, settings: Settings) -> str:
    lo, hi = settings.custom_range
    if lo <= report_id <= hi:
        return "custom"
    tags = [t.strip() for t in version_list.split(",") if t.strip()]
    nav_tags = [t for t in tags if t.upper().startswith("NAV")]
    other_tags = [t for t in tags if not t.upper().startswith("NAV")]
    if not nav_tags and tags:
        return "addon"
    if modified or other_tags:
        return "standard_modified"
    return "standard"


ORIGIN_LABELS = {
    "custom": "Client",
    "standard": "Standard non modifié",
    "standard_modified": "Standard modifié",
    "addon": "Add-on tiers",
}


def propose_strategy(a: ReportAnalysis) -> dict:
    if "Classic" in a.layouts:
        return {
            "code": "classic_skeleton",
            "label": "Squelette RDLC + reprise manuelle",
            "detail": "Sections Classic sans équivalent direct en RDLC. Hors MVP : préférer repartir "
                      "d'un rapport standard BC quand il existe.",
        }
    if a.origin == "standard":
        return {
            "code": "use_standard",
            "label": "Standard BC, rien à migrer",
            "detail": "Aucune modification détectée par rapport au standard.",
        }
    if a.origin == "standard_modified":
        return {
            "code": "report_extension",
            "label": "Report extension sur le standard BC",
            "detail": "Repartir du rapport standard de la version cible et réappliquer uniquement le "
                      "delta client (reportextension + nouveau layout). Rapport standard BC "
                      "correspondant : à confirmer.",
        }
    if a.origin == "addon":
        return {
            "code": "addon_check",
            "label": "Vérifier l'add-on",
            "detail": "Vérifier si l'éditeur fournit une version Business Central avant toute conversion.",
        }
    if a.processing_only:
        return {
            "code": "txt2al_processing",
            "label": "Conversion Txt2AL (traitement)",
            "detail": "Rapport de traitement sans layout : conversion de la logique uniquement.",
        }
    if a.default_layout == "Word" or ("Word" in a.layouts and "RDLC" not in a.layouts):
        return {
            "code": "word_remap",
            "label": "Conversion Txt2AL + remappage Word",
            "detail": "Conversion de l'objet puis remappage des contrôles de contenu du layout Word.",
        }
    detail = "Conversion de l'objet par Txt2AL, post-traitement (labels, rendering, champs) puis compilation."
    if a.complexity_class in ("complex", "highly_complex"):
        detail += " Revue de la logique métier recommandée."
    return {"code": "txt2al_conversion", "label": "Conversion Txt2AL", "detail": detail}


# --------------------------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------------------------

def _collect_dependencies(a: ReportAnalysis, texts: list[str]) -> None:
    deps: dict[str, set[int]] = {"tables": set(), "codeunits": set(), "reports": set(), "pages": set(),
                                 "xmlports": set(), "queries": set()}
    key_map = {"record": "tables", "codeunit": "codeunits", "report": "reports", "page": "pages",
               "xmlport": "xmlports", "query": "queries"}
    for di in a.dataitems:
        if di.get("table_id"):
            deps["tables"].add(di["table_id"])
    for text in texts:
        for kind, num in TYPED_REF_RE.findall(text):
            deps[key_map[kind.lower()]].add(int(num))
        for kind, num in RUN_REF_RE.findall(text):
            deps[key_map[kind.lower()]].add(int(num))
    deps["reports"].discard(a.report_id)
    deps["tables"].discard(INTEGER_TABLE_ID)
    a.dependencies = {k: sorted(v) for k, v in deps.items() if v}


def _apply_detection_rules(a: ReportAnalysis, code_text: str) -> None:
    for rule in DETECTION_RULES:
        rx = re.compile(rule["pattern"], re.IGNORECASE)
        hits: list[str] = []
        if rule["target"] == "var_type":
            hits = [g["name"] for g in a.globals if rx.search(g["type"].replace("TEMPORARY ", ""))]
        elif rule["target"] == "code":
            hits = sorted({m.group(0) for m in rx.finditer(code_text)})
        if hits:
            a.risks.append({
                "rule_id": rule["id"], "category": rule["category"], "severity": rule["severity"],
                "message": rule["message"], "action": rule["action"], "evidence": hits[:10],
                "validation_status": rule["validation_status"],
                "documentation_url": rule.get("documentation_url"),
            })


def _analyze_dataset(a: ReportAnalysis, obj: CalObject) -> list[str]:
    texts: list[str] = []
    elements = parse_dataset(obj.section("DATASET"))
    stack: list[dict] = []
    for el in elements:
        if el.element_type == "DataItem":
            props = el.properties
            table_match = TABLE_RE.search(props.get("DataItemTable", ""))
            table_id = int(table_match.group(1)) if table_match else None
            di = {
                "element_id": el.element_id,
                "name": el.name or (f"Table{table_id}" if table_id else f"DataItem{el.element_id}"),
                "indent": el.indent,
                "table_id": table_id,
                "is_integer": table_id == INTEGER_TABLE_ID,
                "table_view": props.get("DataItemTableView"),
                "link": props.get("DataItemLink"),
                "link_reference": props.get("DataItemLinkReference"),
                "req_filter_fields": props.get("ReqFilterFields"),
                "print_only_if_detail": props.get("PrintOnlyIfDetail") == "Yes",
                "triggers": [],
                "column_count": 0,
            }
            for name, value in props.items():
                if name in TRIGGER_NAMES and _is_trigger_value(value):
                    lines = _code_lines(value)
                    di["triggers"].append(name)
                    a.triggers.append({"scope": di["name"], "trigger": name, "lines": lines})
                    texts.append(value)
            while stack and stack[-1]["indent"] >= el.indent:
                stack.pop()
            di["parent"] = stack[-1]["name"] if stack else None
            stack.append(di)
            a.dataitems.append(di)
        elif el.element_type == "Column":
            parent = None
            for di in reversed(a.dataitems):
                if di["indent"] < el.indent:
                    parent = di
                    break
            if parent:
                parent["column_count"] += 1
            a.columns.append({
                "name": el.name,
                "source_expr": el.properties.get("SourceExpr", ""),
                "dataitem": parent["name"] if parent else None,
                "include_caption": el.properties.get("IncludeCaption") == "Yes",
            })
            texts.append(el.properties.get("SourceExpr", ""))
    return texts


def _analyze_classic(a: ReportAnalysis, obj: CalObject) -> list[str]:
    lines = obj.section("DATAITEMS")
    text = "\n".join(lines)
    tables = [int(t) for t in re.findall(r"DataItemTable=Table(\d+)", text)]
    section_types = re.findall(r"SectionType=([\w ]+?);", text)
    controls = sum(1 for ln in lines if CLASSIC_CONTROL_RE.match(ln))
    for i, table_id in enumerate(tables, start=1):
        a.dataitems.append({
            "element_id": i, "name": f"Table{table_id}", "indent": 0, "table_id": table_id,
            "is_integer": table_id == INTEGER_TABLE_ID, "table_view": None, "link": None,
            "link_reference": None, "req_filter_fields": None, "print_only_if_detail": False,
            "triggers": [], "column_count": 0, "parent": None,
        })
    for trig in TRIGGER_NAMES:
        for m in re.finditer(rf"{trig}=(BEGIN|VAR)", text):
            a.triggers.append({"scope": "Classic", "trigger": trig, "lines": 0})
    a.classic = {
        "dataitem_count": len(tables),
        "section_count": len(section_types),
        "section_types": sorted(set(s.strip() for s in section_types)),
        "control_count": controls,
    }
    return [text]


def analyze_report(obj: CalObject, settings: Settings | None = None) -> ReportAnalysis:
    settings = settings or Settings()
    meta = parse_properties(obj.section("OBJECT-PROPERTIES"))
    props = parse_properties(obj.section("PROPERTIES"))
    a = ReportAnalysis(report_id=obj.object_id, name=obj.name)
    a.version_list = meta.get("Version List", "")
    a.modified = meta.get("Modified", "No") == "Yes"
    a.origin = classify_origin(a.report_id, a.version_list, a.modified, settings)
    a.processing_only = props.get("ProcessingOnly") == "Yes"
    a.default_layout = props.get("DefaultLayout")

    texts: list[str] = []
    for name, value in props.items():
        if name in TRIGGER_NAMES and _is_trigger_value(value):
            a.triggers.append({"scope": "Report", "trigger": name, "lines": _code_lines(value)})
            texts.append(value)

    if obj.has("DATAITEMS") and "SECTIONS" in "\n".join(obj.section("DATAITEMS")):
        a.layouts.append("Classic")
        texts += _analyze_classic(a, obj)
    if obj.has("DATASET"):
        texts += _analyze_dataset(a, obj)

    code = parse_code(obj.section("CODE"))
    a.globals = [{"name": g.name, "id": g.var_id, "type": g.data_type} for g in code.globals]
    a.procedures = [{"name": p.name, "id": p.proc_id, "local": p.local, "lines": p.line_count}
                    for p in code.procedures]
    a.temporary_tables = [g.name for g in code.globals if g.is_temporary]
    texts.append(code.text)

    request_lines = obj.section("REQUESTPAGE") or obj.section("REQUESTFORM")
    a.request_page_fields = sum(1 for ln in request_lines if re.search(r";\s*Field\s*;", ln))
    texts.append("\n".join(request_lines))
    for name, value in parse_properties([ln for ln in request_lines if "=" in ln]).items():
        if name in TRIGGER_NAMES and _is_trigger_value(value):
            a.triggers.append({"scope": "RequestPage", "trigger": name, "lines": _code_lines(value)})

    a.labels = re.findall(r"^\s+\{ ?\d+\s*;(\w+)\s*;", "\n".join(obj.section("LABELS")), re.MULTILINE)

    if obj.has("RDLDATA"):
        a.layouts.append("RDLC")
        a.rdlc = analyze_rdlc("\n".join(obj.section("RDLDATA"))).to_dict()
    if obj.has("WORDLAYOUT"):
        a.layouts.append("Word")
        a.word = analyze_word_layout(obj.section("WORDLAYOUT")).to_dict()

    all_code = "\n".join(texts)
    _collect_dependencies(a, texts + [g["type"] for g in a.globals])
    _apply_detection_rules(a, all_code)
    _check_consistency(a, obj)
    _score(a)
    a.strategy = propose_strategy(a)
    a.strategy["origin_label"] = ORIGIN_LABELS[a.origin]
    a.strategy["status"] = "proposition"
    return a


# --------------------------------------------------------------------------------------------
# Consistency checks between the C/AL dataset and the layouts (Validation Engine, level 1)
# --------------------------------------------------------------------------------------------

def _column_known(name: str, columns: set[str]) -> bool:
    if name in columns:
        return True
    for suffix in ("Format", "Caption"):
        if name.endswith(suffix) and name[: -len(suffix)] in columns:
            return True
    return False


def _check_consistency(a: ReportAnalysis, obj: CalObject) -> None:
    columns = {c["name"] for c in a.columns}
    if a.rdlc is not None:
        if not a.rdlc["valid_xml"]:
            a.issues.append(Issue("error", "RDLC_INVALID_XML", f"Layout RDLC illisible : {a.rdlc['xml_error']}"))
        else:
            rdlc = analyze_rdlc("\n".join(obj.section("RDLDATA")))
            for fname, data_field in sorted(rdlc.dataset_fields.items()):
                if columns and not _column_known(data_field, columns):
                    a.issues.append(Issue("error", "LAYOUT_FIELD_NOT_IN_DATASET",
                                          f"Champ « {data_field} » du layout absent du dataset C/AL."))
            for ref in sorted(rdlc.referenced_fields):
                if ref not in rdlc.dataset_fields:
                    a.issues.append(Issue("error", "EXPRESSION_UNKNOWN_FIELD",
                                          f"Expression qui référence « {ref} », absent du dataset du layout."))
            used = set(rdlc.dataset_fields.values())
            unused = sorted(c for c in columns if c not in used
                            and f"{c}Format" not in used and f"{c}Caption" not in used)
            if unused and "Word" not in a.layouts:
                a.issues.append(Issue("warning", "UNUSED_COLUMNS",
                                      f"{len(unused)} colonne(s) du dataset non utilisée(s) par le layout : "
                                      + ", ".join(unused[:8]) + ("…" if len(unused) > 8 else "")))
            missing_labels = sorted(p for p in rdlc.referenced_parameters if p not in set(a.labels))
            if missing_labels:
                a.issues.append(Issue("warning", "PARAMETER_NOT_A_LABEL",
                                      "Paramètres du layout sans label correspondant : " + ", ".join(missing_labels)))
            if rdlc.uses_code_getdata:
                a.issues.append(Issue("warning", "CODE_GETDATA",
                                      "Code.SetData / GetData utilisés : à remplacer par des colonnes du dataset."))
            if rdlc.barcode_fonts:
                a.issues.append(Issue("warning", "BARCODE_FONT",
                                      "Police(s) de code-barres installée(s) localement : "
                                      + ", ".join(sorted(rdlc.barcode_fonts))
                                      + ". À remplacer par une génération de code-barres côté BC."))
    if a.word is not None:
        if not a.word["decodable"]:
            a.issues.append(Issue("error", "WORD_UNREADABLE", f"Layout Word illisible : {a.word['error']}"))
        else:
            for name in a.word["bound_fields"]:
                if columns and not _column_known(name, columns) and name not in a.labels:
                    a.issues.append(Issue("error", "WORD_FIELD_NOT_IN_DATASET",
                                          f"Contrôle Word lié à « {name} », absent du dataset."))
    for risk in a.risks:
        sev = "error" if risk["severity"] == "high" else "warning"
        a.issues.append(Issue(sev, risk["rule_id"], risk["message"]))


# --------------------------------------------------------------------------------------------
# Complexity score (transparent factors)
# --------------------------------------------------------------------------------------------

def _score(a: ReportAnalysis) -> None:
    f: list[Factor] = []

    def add(label: str, points: int, cap: int, evidence: str) -> None:
        pts = min(points, cap)
        if pts > 0:
            f.append(Factor(label, pts, evidence))

    n_di = len(a.dataitems)
    add("DataItems", 2 * max(n_di - 1, 0), 12, f"{n_di} DataItem(s)")
    n_int = sum(1 for d in a.dataitems if d["is_integer"])
    add("DataItems Integer (itération personnalisée)", 3 * n_int, 9, f"{n_int} DataItem(s) Integer")
    add("Colonnes du dataset", len(a.columns) // 10, 8, f"{len(a.columns)} colonne(s)")
    code_triggers = [t for t in a.triggers if t["lines"] > 0 or t["scope"] == "Classic"]
    add("Triggers avec code", 2 * len(code_triggers), 14, f"{len(code_triggers)} trigger(s)")
    total_lines = sum(t["lines"] for t in a.triggers) + sum(p["lines"] for p in a.procedures)
    add("Volume de code", total_lines // 40, 10, f"{total_lines} ligne(s) de code")
    add("Fonctions", 2 * len(a.procedures), 10, f"{len(a.procedures)} fonction(s)")
    add("Tables temporaires", 3 * len(a.temporary_tables), 9, ", ".join(a.temporary_tables) or "aucune")
    n_deps = sum(len(v) for v in a.dependencies.values())
    add("Dépendances", n_deps, 8, f"{n_deps} objet(s) référencé(s)")
    high = [r for r in a.risks if r["severity"] == "high"]
    add("Fonctions indisponibles en SaaS / Classic", 5 * len(high), 15,
        ", ".join(r["rule_id"] for r in high) or "aucune")
    if "Classic" in a.layouts:
        add("Layout Classic (Sections)", 20, 20, f"{a.classic['section_count']} section(s)")
    if a.rdlc:
        if a.rdlc.get("uses_code_getdata"):
            add("Code.SetData / GetData", 4, 4, "mécanisme à reconstruire")
        if a.rdlc.get("embedded_code_lines"):
            add("Code VB embarqué dans le RDLC", 3, 3, f"{a.rdlc['embedded_code_lines']} ligne(s)")
        if a.rdlc.get("barcode_fonts"):
            add("Polices de codes-barres", 3, 3, ", ".join(a.rdlc["barcode_fonts"]))
        add("Groupes et tablix", (a.rdlc.get("group_count", 0) + a.rdlc.get("tablix_count", 0)) // 3, 6,
            f"{a.rdlc.get('tablix_count', 0)} tablix, {a.rdlc.get('group_count', 0)} groupe(s)")
    if "Word" in a.layouts:
        add("Layout Word", 2, 2, "remappage des contrôles de contenu")

    a.factors = f
    a.complexity_score = sum(x.points for x in f)
    for threshold, code, label in COMPLEXITY_CLASSES:
        if a.complexity_score < threshold:
            a.complexity_class, a.complexity_label = code, label
            break
    else:
        a.complexity_class, a.complexity_label = HIGHLY_COMPLEX


def analyze_export(text: str, settings: Settings | None = None) -> list[ReportAnalysis]:
    """Analyse every report object in a C/AL text export. Other object types are ignored."""
    return [analyze_report(o, settings) for o in split_objects(text) if o.object_type.lower() == "report"]


def summarize(analyses: list[ReportAnalysis]) -> dict:
    by_class: dict[str, int] = {}
    by_layout: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    for a in analyses:
        by_class[a.complexity_label] = by_class.get(a.complexity_label, 0) + 1
        for layout in a.layouts or ["Aucun"]:
            by_layout[layout] = by_layout.get(layout, 0) + 1
        by_strategy[a.strategy["label"]] = by_strategy.get(a.strategy["label"], 0) + 1
    return {
        "report_count": len(analyses),
        "by_complexity": by_class,
        "by_layout": by_layout,
        "by_strategy": by_strategy,
        "error_count": sum(1 for a in analyses for i in a.issues if i.severity == "error"),
        "warning_count": sum(1 for a in analyses for i in a.issues if i.severity == "warning"),
    }

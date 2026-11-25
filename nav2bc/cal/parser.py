"""Parser for Microsoft Dynamics NAV C/AL text exports (.txt).

The C/SIDE text export is indentation-structured:

* every object starts with ``OBJECT <Type> <Id> <Name>`` at column 0 and ends with ``}`` at column 0;
* object sections (``OBJECT-PROPERTIES``, ``PROPERTIES``, ``DATASET``, ``CODE`` ...) start at
  two spaces of indentation and are delimited by ``  {`` / ``  }``;
* ``RDLDATA`` and ``WORDLAYOUT`` contain raw payloads terminated by ``END_OF_RDLDATA`` /
  ``END_OF_WORDLAYOUT``.

The parser is deliberately line based (not brace based) because C/AL comments use ``{ }``
and would break naive brace matching.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OBJECT_RE = re.compile(r"^OBJECT (\w+) (\d+) ?(.*)$")
SECTION_RE = re.compile(r"^  ([A-Z][A-Z\-]+)\s*$")
RAW_SECTIONS = {"RDLDATA": "END_OF_RDLDATA", "WORDLAYOUT": "END_OF_WORDLAYOUT"}

# Dataset element: { <id> ;<indent> ;<type> ;<name> ;<rest>
ELEMENT_RE = re.compile(r"^    \{ ?(\d+)\s*;\s*(\d*)\s*;(\w+)\s*;([^;]*?)\s*;(.*)$")
PROPERTY_RE = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9 \-]*?)=(.*)$")
GLOBAL_VAR_RE = re.compile(r"^\s{6}([\w\"][^@]*?)@(\d+)\s*:\s*(.+?);\s*$")
PROCEDURE_RE = re.compile(r"^\s{4}(LOCAL )?PROCEDURE ([\w\"][^@]*?)@(\d+)\((.*)$")


@dataclass
class CalObject:
    """One object of a C/AL text export, split into raw sections."""

    object_type: str
    object_id: int
    name: str
    sections: dict[str, list[str]] = field(default_factory=dict)

    def section(self, name: str) -> list[str]:
        return self.sections.get(name, [])

    def has(self, name: str) -> bool:
        return name in self.sections


def split_objects(text: str) -> list[CalObject]:
    """Split a (possibly multi-object) export into objects and their sections."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    objects: list[CalObject] = []
    current: CalObject | None = None
    section_name: str | None = None
    section_lines: list[str] = []
    raw_end: str | None = None
    awaiting_open = False

    for line in lines:
        if current is None:
            m = OBJECT_RE.match(line)
            if m:
                current = CalObject(m.group(1), int(m.group(2)), m.group(3).strip())
            continue

        if raw_end is not None:  # inside RDLDATA / WORDLAYOUT payload
            if line.strip() == raw_end:
                raw_end = None
                continue
            section_lines.append(line)
            continue

        if section_name is None:
            if line == "}":  # end of object
                objects.append(current)
                current = None
                continue
            m = SECTION_RE.match(line)
            if m:
                section_name = m.group(1)
                section_lines = []
                awaiting_open = True
            continue

        if awaiting_open:
            awaiting_open = False
            if line.strip() == "{":
                if section_name in RAW_SECTIONS:
                    raw_end = RAW_SECTIONS[section_name]
                continue
        if line == "  }":
            current.sections[section_name] = section_lines
            section_name = None
            continue
        section_lines.append(line)

    if current is not None:  # tolerate a truncated last object
        if section_name is not None:
            current.sections[section_name] = section_lines
        objects.append(current)
    return objects


def parse_properties(lines: list[str]) -> dict[str, str]:
    """Parse ``Name=Value;`` properties; multi-line values (triggers) are kept whole."""
    props: dict[str, str] = {}
    base_indent: int | None = None
    key: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if key is not None:
            value = "\n".join(buf).rstrip()
            if value.endswith(";"):
                value = value[:-1]
            props[key] = value.strip()

    for line in lines:
        if not line.strip():
            if key is not None:
                buf.append("")
            continue
        m = PROPERTY_RE.match(line)
        indent = len(line) - len(line.lstrip())
        if base_indent is None and m:
            base_indent = indent
        if m and indent == base_indent:
            flush()
            key = m.group(2)
            buf = [m.group(3)]
        elif key is not None:
            buf.append(line.strip())
    flush()
    return props


@dataclass
class DatasetElement:
    element_id: int
    indent: int
    element_type: str  # DataItem | Column
    name: str
    properties: dict[str, str]


def parse_dataset(lines: list[str]) -> list[DatasetElement]:
    """Parse the DATASET section of a NAV 2013+ report."""
    chunks: list[tuple[re.Match, list[str]]] = []
    for line in lines:
        m = ELEMENT_RE.match(line)
        if m:
            chunks.append((m, []))
        elif chunks:
            chunks[-1][1].append(line)

    elements: list[DatasetElement] = []
    for m, rest in chunks:
        body = []
        first = m.group(5).strip()
        if first:
            indent = 15
            for candidate in rest:
                pm = PROPERTY_RE.match(candidate)
                if pm:
                    indent = len(pm.group(1))
                    break
            body.append(" " * indent + first)
        body.extend(rest)
        # drop the closing brace of the element
        while body and not body[-1].strip():
            body.pop()
        if body:
            last = body[-1].rstrip()
            if last.endswith("}"):
                body[-1] = last[:-1].rstrip()
        elements.append(
            DatasetElement(
                element_id=int(m.group(1)),
                indent=int(m.group(2) or 0),
                element_type=m.group(3),
                name=m.group(4).strip(),
                properties=parse_properties(body),
            )
        )
    return elements


@dataclass
class GlobalVariable:
    name: str
    var_id: int
    data_type: str

    @property
    def is_temporary(self) -> bool:
        return self.data_type.upper().startswith("TEMPORARY ")


@dataclass
class Procedure:
    name: str
    proc_id: int
    local: bool
    line_count: int = 0


@dataclass
class CodeSection:
    globals: list[GlobalVariable]
    procedures: list[Procedure]
    text: str


def parse_code(lines: list[str]) -> CodeSection:
    globals_: list[GlobalVariable] = []
    procedures: list[Procedure] = []
    in_global_var = False
    current: Procedure | None = None
    for line in lines:
        stripped = line.strip()
        if line.startswith("    VAR") and current is None and not procedures:
            in_global_var = True
            continue
        pm = PROCEDURE_RE.match(line)
        if pm:
            in_global_var = False
            current = Procedure(pm.group(2).strip('"'), int(pm.group(3)), bool(pm.group(1)))
            procedures.append(current)
            continue
        if in_global_var:
            gm = GLOBAL_VAR_RE.match(line)
            if gm:
                globals_.append(GlobalVariable(gm.group(1).strip('"'), int(gm.group(2)), gm.group(3).strip()))
                continue
            if line.startswith("    BEGIN"):
                in_global_var = False
        if current is not None and stripped:
            current.line_count += 1
    return CodeSection(globals_, procedures, "\n".join(lines))

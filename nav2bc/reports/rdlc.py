"""Analysis of RDLC layouts embedded in NAV report exports (RDLDATA section)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

FIELD_REF_RE = re.compile(r"Fields!([A-Za-z_][\w]*)\.Value")
PARAM_REF_RE = re.compile(r"Parameters!([A-Za-z_][\w]*)\.Value")
CODE_DATA_RE = re.compile(r"Code\.(GetData|SetData)\s*\(", re.IGNORECASE)
BARCODE_FONT_RE = re.compile(r"barcode|code\s?128|code\s?39|3\s?of\s?9|idautomation|ean\s?13|\bupc\b", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class RdlcAnalysis:
    valid_xml: bool
    xml_error: str | None = None
    schema_namespace: str | None = None
    dataset_fields: dict[str, str] = field(default_factory=dict)  # field name -> DataField
    referenced_fields: set[str] = field(default_factory=set)
    referenced_parameters: set[str] = field(default_factory=set)
    uses_code_getdata: bool = False
    embedded_code_lines: int = 0
    fonts: set[str] = field(default_factory=set)
    barcode_fonts: set[str] = field(default_factory=set)
    tablix_count: int = 0
    group_count: int = 0
    has_page_header: bool = False
    has_page_footer: bool = False
    image_count: int = 0

    def to_dict(self) -> dict:
        return {
            "valid_xml": self.valid_xml,
            "xml_error": self.xml_error,
            "schema_namespace": self.schema_namespace,
            "dataset_field_count": len(self.dataset_fields),
            "referenced_fields": sorted(self.referenced_fields),
            "referenced_parameters": sorted(self.referenced_parameters),
            "uses_code_getdata": self.uses_code_getdata,
            "embedded_code_lines": self.embedded_code_lines,
            "fonts": sorted(self.fonts),
            "barcode_fonts": sorted(self.barcode_fonts),
            "tablix_count": self.tablix_count,
            "group_count": self.group_count,
            "has_page_header": self.has_page_header,
            "has_page_footer": self.has_page_footer,
            "image_count": self.image_count,
        }


def analyze_rdlc(xml_text: str) -> RdlcAnalysis:
    xml_text = xml_text.strip()
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as exc:
        return RdlcAnalysis(valid_xml=False, xml_error=str(exc))

    result = RdlcAnalysis(valid_xml=True)
    if root.tag.startswith("{"):
        result.schema_namespace = root.tag[1:].split("}", 1)[0]

    for el in root.iter():
        tag = _local(el.tag)
        text = (el.text or "").strip()
        if tag == "Field" and el.get("Name"):
            data_field = next((c.text for c in el if _local(c.tag) == "DataField"), None)
            result.dataset_fields[el.get("Name")] = (data_field or el.get("Name")).strip()
        elif tag == "Tablix":
            result.tablix_count += 1
        elif tag == "Group" and el.get("Name"):
            result.group_count += 1
        elif tag == "PageHeader":
            result.has_page_header = True
        elif tag == "PageFooter":
            result.has_page_footer = True
        elif tag == "Image":
            result.image_count += 1
        elif tag == "Code" and text:
            result.embedded_code_lines = len([ln for ln in text.splitlines() if ln.strip()])
            if CODE_DATA_RE.search(text):
                result.uses_code_getdata = True
        elif tag == "FontFamily" and text and not text.startswith("="):
            result.fonts.add(text)
            if BARCODE_FONT_RE.search(text):
                result.barcode_fonts.add(text)

        if text.startswith("="):
            result.referenced_fields.update(FIELD_REF_RE.findall(text))
            result.referenced_parameters.update(PARAM_REF_RE.findall(text))
            if CODE_DATA_RE.search(text):
                result.uses_code_getdata = True
    return result

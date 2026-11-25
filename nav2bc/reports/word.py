"""Analysis of Word layouts embedded in NAV report exports (WORDLAYOUT section).

NAV stores the .docx base64-encoded. Content controls are bound to the report dataset through
``w:dataBinding w:xpath="/ns0:NavWordReportXmlPart[1]/ns0:Header[1]/ns0:Field[1]"``.
"""
from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile
from dataclasses import dataclass, field

XPATH_RE = re.compile(r'w:xpath="([^"]+)"')
SDT_RE = re.compile(r"<w:sdt[ >]")


@dataclass
class WordAnalysis:
    decodable: bool
    error: str | None = None
    size_bytes: int = 0
    content_controls: int = 0
    bound_fields: set[str] = field(default_factory=set)
    unbound_controls: int = 0

    def to_dict(self) -> dict:
        return {
            "decodable": self.decodable,
            "error": self.error,
            "size_bytes": self.size_bytes,
            "content_controls": self.content_controls,
            "bound_fields": sorted(self.bound_fields),
            "unbound_controls": self.unbound_controls,
        }


def analyze_word_layout(payload_lines: list[str]) -> WordAnalysis:
    b64 = "".join(line.strip() for line in payload_lines)
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        return WordAnalysis(decodable=False, error=f"base64 invalide : {exc}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            document = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        return WordAnalysis(decodable=False, error=f"docx illisible : {exc}", size_bytes=len(data))

    result = WordAnalysis(decodable=True, size_bytes=len(data))
    result.content_controls = len(SDT_RE.findall(document))
    xpaths = XPATH_RE.findall(document)
    for xp in xpaths:
        last = xp.rstrip("/").split("/")[-1]
        name = re.sub(r"\[\d+\]$", "", last).split(":")[-1]
        if name:
            result.bound_fields.add(name)
    result.unbound_controls = max(result.content_controls - len(xpaths), 0)
    return result

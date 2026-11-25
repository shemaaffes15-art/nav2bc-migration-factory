"""Error signatures: reduce a raw tool message to a stable key so the same error is recognised
across objects, projects and clients."""
from __future__ import annotations

import hashlib
import re

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?"), "<GUID>"),
    (re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s'\"]+"), "<PATH>"),
    (re.compile(r"(?<!\w)'[^']*'(?!\w)"), "'<NAME>'"),
    (re.compile(r"\"[^\"]*\""), "\"<NAME>\""),
    (re.compile(r"«[^»]*»"), "«<NAME>»"),
    (re.compile(r"\(\d+,\d+\)"), "(<POS>)"),
    (re.compile(r"\b\d+(?:[.,]\d+)*\b"), "<N>"),
]


def normalize(message: str) -> str:
    """Replace the variable parts of a message (names, numbers, paths, GUIDs) by placeholders.

    Error codes such as ``AL0185`` are kept: digits glued to letters are not numbers.
    """
    text = message.strip()
    for rx, repl in _RULES:
        text = rx.sub(repl, text)
    return re.sub(r"\s+", " ", text)


def signature_key(module: str, message: str) -> str:
    norm = normalize(message)
    digest = hashlib.sha1(f"{module.lower()}|{norm}".encode("utf-8")).hexdigest()[:16]
    return digest

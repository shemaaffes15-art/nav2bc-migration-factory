"""Upgrade path calculation NAV / BC on-premises → BC (on-premises or Online).

Each hop carries its evidence. ``documented`` hops point to the Microsoft page that supports
them; ``to_verify`` hops must be confirmed before the plan is validated. The path must be
re-checked against the documentation when a project is created: Microsoft changes the
supported paths with each release.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

DOC_UPGRADE_PATHS = "https://learn.microsoft.com/dynamics365/business-central/dev-itpro/upgrade/upgrade-paths"
DOC_UPGRADE_V14 = "https://learn.microsoft.com/dynamics365/business-central/dev-itpro/upgrade/upgrading-to-business-central-on-premises"
DOC_MIGRATE_NAV = "https://learn.microsoft.com/dynamics365/business-central/dev-itpro/administration/migrate-nav"

NAV_YEARS = {"2009": 2009, "2013": 2013, "2015": 2015, "2016": 2016, "2017": 2017, "2018": 2018}


@dataclass
class SourceVersion:
    product: str  # "NAV" | "BC"
    number: int  # NAV year or BC major version
    label: str


@dataclass
class Hop:
    order: int
    kind: str  # upgrade | conversion | cloud_migration
    from_version: str
    to_version: str
    requirement: str  # mandatory | recommended
    evidence: str  # documented | to_verify | factory_recommendation
    description: str
    documentation_url: str | None = None


class UnsupportedVersion(ValueError):
    pass


def parse_version(text: str) -> SourceVersion:
    t = text.strip().upper().replace(" ", "")
    m = re.fullmatch(r"(?:DYNAMICS)?NAV(\d{4})(R2)?", t)
    if m and m.group(1) in NAV_YEARS:
        return SourceVersion("NAV", NAV_YEARS[m.group(1)], f"NAV {m.group(1)}{' R2' if m.group(2) else ''}")
    m = re.fullmatch(r"(?:BC|BUSINESSCENTRAL)?(\d{1,2})", t)
    if m and 13 <= int(m.group(1)) <= 40:
        return SourceVersion("BC", int(m.group(1)), f"BC {int(m.group(1))}")
    raise UnsupportedVersion(f"Version non reconnue : {text!r} (ex. NAV2016, NAV2009R2, BC14, BC23)")


def compute_path(source: str, target_major: int, online: bool = True) -> dict:
    src = parse_version(source)
    hops: list[Hop] = []

    def add(**kw) -> None:
        hops.append(Hop(order=len(hops) + 1, **kw))

    current_label = src.label
    if src.product == "NAV" and src.number == 2009:
        add(kind="upgrade", from_version=current_label, to_version="NAV 2013", requirement="mandatory",
            evidence="to_verify",
            description="Étape intermédiaire pour une source NAV 2009 : upgrade technique et données vers NAV 2013.")
        current_label = "NAV 2013"

    if src.product == "NAV" or (src.product == "BC" and src.number == 13):
        add(kind="upgrade", from_version=current_label, to_version="BC 14", requirement="mandatory",
            evidence="documented",
            description="Dernière version C/AL : conversion de la base, export TXT, conversion du code avec Txt2AL.",
            documentation_url=DOC_UPGRADE_V14)
        current = 14
    else:
        current = src.number
        if current == 14:
            add(kind="conversion", from_version="BC 14 (C/AL)", to_version="BC 14 (AL)", requirement="mandatory",
                evidence="documented",
                description="Conversion des personnalisations C/AL en extensions AL avant de quitter la v14.",
                documentation_url=DOC_UPGRADE_V14)

    if target_major < current:
        raise UnsupportedVersion(f"La cible BC {target_major} est antérieure à la source ({src.label}).")

    if current < 25 and target_major >= 26:
        add(kind="upgrade", from_version=f"BC {current}", to_version="BC 25", requirement="mandatory",
            evidence="documented",
            description="Pas de saut direct vers la v26 et plus depuis les versions 14 à 24 : passage par la v25.",
            documentation_url=DOC_UPGRADE_PATHS)
        current = 25

    if current < target_major:
        add(kind="upgrade", from_version=f"BC {current}", to_version=f"BC {target_major}",
            requirement="recommended" if online else "mandatory",
            evidence="factory_recommendation" if online else "documented",
            description=("Aligner la version on-premises sur la version Online pour tester les extensions."
                         if online else "Upgrade de l'application et des données vers la version cible."),
            documentation_url=None if online else DOC_UPGRADE_PATHS)

    if online:
        add(kind="cloud_migration", from_version="BC on-premises", to_version=f"BC Online {target_major}",
            requirement="mandatory", evidence="documented",
            description="Migration cloud par le runtime d'intégration ; seules les tables présentes à la fois "
                        "dans la source et dans BC Online sont migrées.",
            documentation_url=DOC_MIGRATE_NAV)

    upgrades = [h for h in hops if h.kind == "upgrade"]
    return {
        "source": src.label,
        "target": f"BC {'Online ' if online else ''}{target_major}",
        "online": online,
        "hops": [asdict(h) for h in hops],
        "upgrade_count": len(upgrades),
        "summary": f"{len(upgrades)} mise(s) à niveau on-premises" + (" + migration cloud" if online else ""),
        "to_verify": [h.order for h in hops if h.evidence == "to_verify"],
    }

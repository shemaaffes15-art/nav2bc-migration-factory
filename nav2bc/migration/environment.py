"""Project environment: where the tools, services and databases live on the migration machine.

Every value is editable from the interface (screen « Environnement »). Defaults follow the
standard Microsoft installation folders; they must be checked on the actual machine.
"""
from __future__ import annotations

import re

# key, label, group, default (may use {slug}), help
FIELDS: list[tuple[str, str, str, str, str]] = [
    ("workdir", "Dossier de travail", "Général", r"C:\NAV2BC\{slug}",
     "Exports, scripts générés, journaux et fichiers AL du projet."),
    ("sql_server", "Serveur SQL", "Base de données", "localhost",
     "Instance SQL Server qui héberge la base NAV (ex. localhost\\SQLEXPRESS)."),
    ("nav_database", "Base NAV source", "Base de données", "",
     "Nom de la base NAV d'origine. Elle n'est jamais modifiée : on travaille sur une copie."),
    ("work_database", "Base de travail (copie)", "Base de données", "{slug}_UPG",
     "Copie restaurée de la base NAV, sur laquelle tous les sauts de version sont appliqués."),
    ("backup_dir", "Dossier des sauvegardes SQL", "Base de données", r"C:\NAV2BC\{slug}\backup",
     "Doit être accessible en écriture par le compte du service SQL Server."),
    ("nav_finsql", "finsql.exe NAV source", "NAV source", "",
     r"Ex. C:\Program Files (x86)\Microsoft Dynamics NAV\90\RoleTailored Client\finsql.exe"),
    ("bc14_finsql", "finsql.exe BC14", "BC 14", r"C:\Program Files (x86)\Microsoft Dynamics 365 Business Central\140\RoleTailored Client\finsql.exe", ""),
    ("bc14_admin", "NavAdminTool.ps1 BC14", "BC 14", r"C:\Program Files\Microsoft Dynamics 365 Business Central\140\Service\NavAdminTool.ps1", ""),
    ("bc14_model_tools", "Model Tools BC14", "BC 14", r"C:\Program Files (x86)\Microsoft Dynamics 365 Business Central\140\RoleTailored Client\Microsoft.Dynamics.Nav.Model.Tools.psd1",
     "Module des cmdlets Merge-NAVApplicationObject et Split-NAVApplicationObjectFile."),
    ("bc14_txt2al", "txt2al.exe", "BC 14", r"C:\Program Files (x86)\Microsoft Dynamics 365 Business Central\140\RoleTailored Client\txt2al.exe",
     "Prendre la version de txt2al indiquée par la documentation Microsoft pour la version cible."),
    ("bc14_instance", "Instance de service BC14", "BC 14", "BC140", ""),
    ("bc14_license", "Licence (BC14)", "BC 14", "", "Fichier .flf de licence partenaire/client pour BC14."),
    ("original_std_txt", "Standard NAV d'origine (TXT)", "Fusion du code", "",
     "Export TXT du standard NAV de la même version et localisation que la base client (version « Original »)."),
    ("target_std_txt", "Standard BC14 (TXT)", "Fusion du code", "",
     "Export TXT du standard BC14 de la localisation cible (version « Target »)."),
    ("bc25_admin", "NavAdminTool.ps1 BC25", "BC 25", r"C:\Program Files\Microsoft Dynamics 365 Business Central\250\Service\NavAdminTool.ps1", ""),
    ("bc25_instance", "Instance de service BC25", "BC 25", "BC250", ""),
    ("bc25_dvd", "DVD BC25 (dossier)", "BC 25", r"C:\Install\BC25",
     "Dossier du DVD de la mise à jour BC25 : on y cherche System.app et les applications Microsoft."),
    ("bc25_license", "Licence (BC25)", "BC 25", "", "Fichier .bclicense."),
    ("target_admin", "NavAdminTool.ps1 version cible", "Version cible", r"C:\Program Files\Microsoft Dynamics 365 Business Central\{target}0\Service\NavAdminTool.ps1", ""),
    ("target_instance", "Instance de service cible", "Version cible", "BC{target}0", ""),
    ("target_dvd", "DVD version cible (dossier)", "Version cible", r"C:\Install\BC{target}", ""),
    ("target_license", "Licence (version cible)", "Version cible", "", "Fichier .bclicense."),
    ("alc", "alc.exe (compilateur AL)", "AL", "",
     r"Dans l'extension AL de VS Code : %USERPROFILE%\.vscode\extensions\ms-dynamics-smb.al-<version>\bin\win32\alc.exe"),
    ("al_project", "Projet AL client", "AL", r"C:\NAV2BC\{slug}\al\customer-app",
     "Dossier du projet AL (app.json) qui reçoit le code converti par Txt2AL."),
    ("al_packages", "Symboles (.alpackages)", "AL", r"C:\NAV2BC\{slug}\al\customer-app\.alpackages",
     "Symboles de la version cible, téléchargés depuis VS Code (AL: Download symbols)."),
    ("ext_app_id", "ID de l'extension client", "AL", "", "GUID de app.json. Sert au paramètre DestinationAppsForMigration."),
    ("ext_app_name", "Nom de l'extension client", "AL", "", ""),
    ("ext_publisher", "Éditeur de l'extension client", "AL", "", ""),
    ("ext_app_file", "Fichier .app de l'extension client", "AL", r"C:\NAV2BC\{slug}\al\customer-app\output\customer.app", ""),
    ("bc_database_name_online", "Environnement BC Online", "BC Online", "Production",
     "Nom de l'environnement cible dans le centre d'administration BC."),
]

DEFAULT_ID_RANGE = (50000, 99999)


def slugify(name: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return s[:40] or "projet"


def defaults(project: dict) -> dict[str, str]:
    slug = slugify(project["name"])
    target = str(project["target_version"])
    out = {}
    for key, _label, _group, default, _help in FIELDS:
        out[key] = default.replace("{slug}", slug).replace("{target}", target)
    if project.get("source_version"):
        out["nav_database"] = out["nav_database"] or ""
    return out


def describe(project: dict) -> list[dict]:
    d = defaults(project)
    return [{"key": k, "label": label, "group": g, "default": d[k], "help": h} for k, label, g, _df, h in FIELDS]


def merge(project: dict, stored: dict | None) -> dict[str, str]:
    env = defaults(project)
    for k, v in (stored or {}).items():
        if k in env and v is not None:
            env[k] = str(v)
    return env


def missing(env: dict, keys: list[str]) -> list[str]:
    labels = {k: label for k, label, *_ in FIELDS}
    return [labels.get(k, k) for k in keys if not str(env.get(k, "")).strip()]

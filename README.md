# NAV2BC Migration Factory

Plateforme interne qui industrialise les migrations **Microsoft Dynamics NAV → Business Central**
(on-premises ou Online) et capitalise d'un projet à l'autre.

> Statut : **v0.2** — interface complète et moteur de migration (plan d'étapes, scripts PowerShell, exécution nominative, validation)
> Architecture et feuille de route : [`docs/architecture.md`](docs/architecture.md).

## Ce que fait l'application

| Écran | Rôle |
|---|---|
| Tableau de bord | Projets, avancement, prochaine étape, erreurs déjà résolues. |
| Projet & chemin | Version source et cible, chemin de mise à niveau obligatoire (NAV → BC 14 → BC 25 → BC 29 → BC Online), chaque saut avec sa source Microsoft. |
| Environnement | Serveur SQL, bases, finsql, outils d'administration BC 14 / 25 / cible, txt2al, alc, DVD, licences, projet AL. |
| Inventaire | Import de l'export TXT de tous les objets : objets par type et origine, tables client, champs ajoutés aux tables standard, fonctions bloquantes pour BC Online. |
| Rapports & layouts | Analyse de chaque rapport (dataset, RDLC, Word, Classic), contrôles de cohérence, score de complexité justifié, stratégie proposée. |
| Données | Couverture des tables et champs client par l'extension AL (sans elle, la migration cloud ne reprend pas ces données). |
| Exécution | Le plan complet, étape par étape (43 étapes pour NAV 2016 → BC Online 29) : scripts PowerShell générés, modifiables ou remplacés par vos propres scripts, exécution nominative avec journal en direct, confirmation obligatoire pour toute étape qui modifie une base ou un service, étapes manuelles avec liste de contrôle signée. |
| Code AL | Export au nouveau format, Txt2AL, projet AL, compilation alc, erreurs AL capitalisées. |
| Validation | Comptage des enregistrements source / cible et comparaison table par table, recette des rapports. |
| Journal d'erreurs | Chaque erreur d'exécution devient une signature ; quand elle revient, la solution documentée s'affiche avec sa confiance. |
| Référentiel | Fiches de changement versionnées (preuve, portée, validation nominative). |

**Principe** : rien de destructif sans validation nominative ; les scripts générés suivent la documentation Microsoft et doivent être relus ; une hypothèse n'est jamais appliquée automatiquement.

## Démarrer sur la machine de migration (Windows)

L'application exécute les étapes PowerShell **sur la machine où elle tourne**. Installez-la sur le serveur Windows qui a SQL Server, le Development Environment NAV, les versions BC 14 / 25 / cible et l'extension AL de VS Code.

1. Installer Python 3.11+ (cocher « Add python.exe to PATH »).
2. Double-cliquer sur `demarrer.bat` : il crée l'environnement, installe l'application, lance le serveur et ouvre http://127.0.0.1:8000.
3. Créer le projet, renseigner l'écran **Environnement**, puis suivre l'écran **Exécution** dans l'ordre.

Vos propres scripts PowerShell remplacent les scripts générés : déposez-les dans `<dossier de travail>\scripts\<id de l'étape>.ps1` (l'id est affiché sur chaque étape), ou collez-les dans l'éditeur de l'étape.

Sur une machine sans PowerShell (Linux, macOS), les étapes sont simulées : le script est généré et journalisé, rien n'est exécuté.

## Démarrage manuel (développeurs)


Prérequis : Python 3.11+.

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate   —   Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"

# Tests
pytest

# Interface web + API (http://127.0.0.1:8000, documentation API sur /docs)
nav2bc serve

# Ligne de commande
nav2bc analyze-reports chemin/vers/export.txt -v --json analyse.json
nav2bc path NAV2016 29          # chemin vers BC Online 29
nav2bc path BC23 29 --onprem    # chemin on-premises
```

Pour exporter les rapports depuis NAV : Object Designer → filtrer sur les rapports → *File > Export*
au format **.txt**. Un seul fichier peut contenir tous les rapports.

## Organisation du code

```
nav2bc/
  cal/parser.py          parseur des exports TXT C/AL (sections, dataset, code)
  reports/analyzer.py    Report Object Engine : inventaire, score, stratégie, contrôles
  reports/rdlc.py        analyse des layouts RDLC
  reports/word.py        analyse des layouts Word
  knowledge/store.py     projets, analyses, référentiel des changements, erreurs (SQLite)
  knowledge/signature.py normalisation des messages d'erreur
  upgrade_path.py           calcul du chemin de mise à niveau
  migration/plan.py         plan d'étapes et scripts PowerShell par saut de version
  migration/runner.py       exécution (ou simulation), journal, erreurs capitalisées
  migration/environment.py  paramètres de l'environnement de migration
  inventory.py              inventaire complet de l'export TXT
  al_scan.py                lecture du projet AL, couverture des données, comparaison des volumes
  api/app.py             API REST (FastAPI) + interface web (api/static)
  cli.py                 ligne de commande
tests/                   tests + fixtures synthétiques (aucune donnée client réelle)
docs/architecture.md     architecture, modèle de données, MVP / Should / Could
```

## Données clients

Ne jamais committer d'exports réels de clients. Les fixtures de `tests/fixtures` sont synthétiques.
Les dossiers `exports/` et `client-data/` sont ignorés par Git.

## Prochaines étapes (Should)

1. Agent d'exécution Windows : Txt2AL (conteneur BC 14), compilation alc, scripts PowerShell, ingestion des journaux.
2. Post-traitement AL des rapports (labels, section `rendering`, champs déplacés) et remappage Word.
3. Comparaison des datasets NAV ↔ BC sur données identiques.
4. Comparaison de schéma (Assess) et éditeur de mapping (Plan).

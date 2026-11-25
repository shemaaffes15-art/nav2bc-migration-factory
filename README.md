# NAV2BC Migration Factory

Plateforme interne qui industrialise les migrations **Microsoft Dynamics NAV → Business Central**
(on-premises ou Online) et capitalise d'un projet à l'autre.

> Statut : **v0.1 (MVP en cours)** — premier module livré : le moteur de rapports
> (inventaire, analyse, cohérence dataset ↔ layout, stratégie). Architecture et feuille de route :
> [`docs/architecture.md`](docs/architecture.md).

## Ce que fait la v0.1

| Fonction | Détail |
|---|---|
| **Analyse des rapports** | Lit un export TXT C/AL (un ou plusieurs objets) : DataItems et hiérarchie, colonnes, triggers, variables, tables temporaires, fonctions, dépendances, request page, labels. |
| **Layouts** | RDLC (champs du dataset, expressions, `Code.GetData/SetData`, code VB, polices de codes-barres, tablix, groupes), Word (contrôles de contenu liés), Classic NAV 2009 (sections, contrôles). |
| **Cohérence** | Champs du layout absents du dataset, expressions vers des champs inconnus, colonnes inutilisées, labels manquants. |
| **Risques** | Fonctions indisponibles en BC Online ou liées au Classic (File, DotNet, Automation, SHELL, SHOWOUTPUT…), règles dans `nav2bc/data/detection_rules.json`. |
| **Complexité** | Score justifié facteur par facteur → Simple / Moyen / Complexe / Très complexe. |
| **Stratégie proposée** | Standard BC (rien à migrer), report extension sur le standard BC, conversion Txt2AL, remappage Word, squelette RDLC pour le Classic. Toujours une *proposition* à valider. |
| **Chemin de mise à niveau** | Sauts de version obligatoires/recommandés avec leur source Microsoft (ex. NAV 2016 → BC 14 → BC 25 → BC 29 → migration cloud). |
| **Référentiel (Learn)** | Fiches de changement versionnées (preuve, portée, validation nominative) et signatures d'erreur avec solution connue et confiance. |

Principe de base : **une hypothèse n'est jamais appliquée automatiquement**. Seule une entrée validée
nominativement et prouvée (documentation Microsoft, identité d'ID ou validation consultante) est
marquée `auto_applicable`.

## Démarrer

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
  upgrade_path.py        calcul du chemin de mise à niveau
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

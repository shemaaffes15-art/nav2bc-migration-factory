# NAV2BC Migration Factory — Architecture

Document de référence à valider avant d'étendre le développement.
Statut : **proposition v0.1** (à valider par la consultante).

## 1. Vision

Une plateforme qui industrialise les migrations Microsoft Dynamics NAV → Business Central
(on-premises ou Online), en s'appuyant sur les outils et chemins officiels Microsoft, et qui
**capitalise** d'un projet à l'autre : chaque correspondance validée, règle et anomalie résolue
enrichit un référentiel réutilisable.

Principes non négociables :

1. **Le standard Microsoft d'abord** : Txt2AL, upgrade de version, migration cloud sont pilotés, jamais recodés.
2. **Un fait documenté n'est pas une hypothèse** : toute correspondance porte sa preuve
   (documentation Microsoft, identité d'ID) ou le statut `hypothesis`. Un nom similaire n'est jamais une preuve.
3. **Rien d'incertain ou de destructif sans validation humaine** (suppression, fusion, troncature).
4. **Tout est tracé, versionné, réutilisable** : règles, scripts, décisions, exécutions.
5. **Déterministe pour la structure, IA pour l'assistance** : l'IA propose, la consultante valide.

## 2. Cycle de migration (7 phases)

| Phase | Module | Écran maquette |
|---|---|---|
| Discover | Diagnostic, inventaire, score de complexité | 4 |
| Assess | Comparaison de schéma source / cible | 5 |
| Plan | Mapping & règles de transformation | 7 |
| Transform | Code (Txt2AL) et **moteur de rapports** | 8, 8B, 8C |
| Migrate | Exécution par saut de version, ETL, reprise | 9 |
| Validate | Réconciliation, go / no-go | 10 |
| Learn | Référentiel des changements et des erreurs | 6 |

Transversal : assistant projet et chemin officiel (écran 2), orchestration et environnements
DEV / TEST / PROD (écran 3).

## 3. Chemin de mise à niveau

Une source NAV ne passe jamais directement à BC Online. Le chemin est calculé puis rattaché
à sa source officielle (à revérifier à chaque création de projet) :

| Source | Sauts on-premises | Puis |
|---|---|---|
| NAV 2009 / R2 | NAV 2013 → BC 14 → BC 25 → (BC cible, recommandé) | Migration cloud |
| NAV 2013 → 2018, BC 13 | BC 14 → BC 25 → (BC cible) | Migration cloud |
| BC 14 (C/AL) | conversion AL → BC 25 → (BC cible) | Migration cloud |
| BC 15 → 24 | BC 25 → (BC cible) | Migration cloud |
| BC 25 → 28 | (BC cible) | Migration cloud |

Implémentation : `nav2bc/upgrade_path.py`. Les sauts sont des **données** (versionnées dans le
référentiel), pas du code en dur, pour suivre les évolutions Microsoft.

## 4. Architecture technique

```
┌────────────────────────────── Interface web (SPA) ───────────────────────────────┐
│ Tableau de bord · Projet · Discover · Assess · Plan · Transform · Migrate · ...  │
└───────────────────────────────────────┬──────────────────────────────────────────┘
                                        │ REST (JSON)
┌───────────────────────────────────────▼──────────────────────────────────────────┐
│ API (FastAPI)                                                                     │
├──────────────┬────────────────┬───────────────┬──────────────┬───────────────────┤
│ Projets &    │ Moteur de      │ Moteur de     │ Exécution    │ Référentiel        │
│ chemins      │ rapports       │ schéma/mapping│ (runbooks)   │ (Learn)            │
│              │ 1 Objet        │               │ PowerShell   │ changements,       │
│              │ 2 Layout       │               │ par saut     │ règles, erreurs,   │
│              │ 3 Validation   │               │              │ signatures         │
├──────────────┴────────────────┴───────────────┴──────────────┴───────────────────┤
│ Stockage : SQLite (dev) → PostgreSQL (prod) · fichiers projet (exports, AL, RDLC) │
└───────────────────────────────────────┬──────────────────────────────────────────┘
                                        │ agent d'exécution (Windows)
┌───────────────────────────────────────▼──────────────────────────────────────────┐
│ Poste / serveur Windows : BcContainerHelper + Docker, conteneur BC 14 (Txt2AL),   │
│ conteneur BC cible (symboles, alc.exe, exécution des rapports), finsql, PowerShell│
└───────────────────────────────────────────────────────────────────────────────────┘
```

Le cœur (parsing, analyse, génération, règles) est en **Python pur, testable sans BC**.
Tout ce qui exige Business Central passe par un **agent d'exécution Windows** qui lance les
commandes (Txt2AL, alc, scripts PowerShell fournis par la consultante) et renvoie journaux et
résultats à l'API. Les journaux sont toujours analysés par le référentiel (signatures d'erreur).

### Moteur de rapports (prioritaire)

| Moteur | Responsabilité | Statut v0.1 |
|---|---|---|
| 1. Report Object Engine | Parseur TXT C/AL, DataItems, colonnes, triggers, variables, dépendances ; Txt2AL ; dataset | Parseur + analyse **livrés** |
| 2. Layout Transformation Engine | Analyse RDLC / Word, remappage des champs, génération RDLC, section `rendering` | Analyse RDLC **livrée** ; génération à venir |
| 3. Validation Engine | Compilation (alc), validation XML RDLC, comparaison des datasets NAV ↔ BC, comparaison PDF | Cohérence dataset ↔ layout **livrée** |
| Transversal : règles & référentiel | Correspondances, champs obsolètes, corrections réutilisées | Base + signatures **livrées** |

Stratégies par rapport (calculées, toujours modifiables par la consultante) :

| Cas détecté | Stratégie |
|---|---|
| Rapport standard non modifié | Aucune migration : utiliser le standard BC |
| Rapport standard modifié | Repartir du standard BC + `reportextension` du delta client (correspondance à confirmer) |
| Rapport client RDLC | Txt2AL + post-traitement + compilation |
| Rapport avec layout Word | Remappage des contrôles de contenu Word |
| Rapport Classic (Sections, NAV 2009) | Squelette RDLC + reprise manuelle (hors MVP) |

Ordre de validation : **technique** (compilation, XML, champs) → **fonctionnelle** (datasets NAV et
BC sur données identiques : lignes, groupes, totaux) → **visuelle** (PDF, texte et positions).

## 5. Modèle de données (principal)

- `Project` : client, scénario (A/B/C), versions source/cible, environnements, chemin calculé.
- `ReportAnalysis` : rapport, layout, DataItems, colonnes, triggers, dépendances, score + facteurs, stratégie.
- `KnowledgeEntry` (référentiel des changements) : versions source/cible, table/champ source,
  table/champ cible, type de changement, description, URL de documentation, action requise,
  statut, risque, confiance, statut de validation, portée (`microsoft` / `version` / `client` / `project`),
  version de la fiche, historique.
- `ErrorSignature` (Learn) : message normalisé, module, occurrences, solution liée, confiance, retours.
- `Rule` (à venir) : type (copie, renommage, conversion, fusion, éclatement, exclusion…),
  expression, caractère destructif, statut de validation, version.

## 6. Découpage MVP / Should / Could

**MVP (en cours)**
- Inventaire et analyse des rapports depuis un export TXT C/AL (multi-objets).
- Score de complexité justifié et classification (Simple / Moyen / Complexe / Très complexe).
- Détection du type de layout (RDLC, Word, Classic) et stratégie proposée.
- Analyse RDLC : champs du dataset, expressions, `Code.GetData/SetData`, polices de codes-barres,
  cohérence colonnes C/AL ↔ champs RDLC.
- Référentiel : fiches de changement et signatures d'erreur avec recherche de solution connue.
- Calcul du chemin de mise à niveau.
- API REST + CLI + interface d'inventaire.

**Should**
- Agent d'exécution Windows (Txt2AL, alc, scripts PowerShell) et ingestion des journaux.
- Post-traitement AL des rapports (labels, section `rendering`, champs déplacés).
- Remappage des layouts Word.
- Comparaison des datasets NAV ↔ BC.
- Comparaison de schéma (Assess) et éditeur de mapping (Plan).

**Could**
- Comparaison visuelle PDF.
- Génération de squelettes RDLC depuis les Sections Classic.
- Assistance IA (suggestions de mapping, explications d'écarts), toujours soumise à validation.

## 7. Sécurité et données

- Pas d'exports réels de clients dans le dépôt : fixtures synthétiques ou anonymisées uniquement.
- Aucune information d'identification stockée dans le code ; authentification déléguée (Microsoft Entra) pour le tenant cible.
- PROD verrouillé tant que la décision go / no-go n'est pas prise.

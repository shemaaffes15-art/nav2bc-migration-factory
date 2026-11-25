"""Command line: python -m nav2bc <command>."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api.app import _decode
from .reports.analyzer import analyze_export, summarize
from .upgrade_path import UnsupportedVersion, compute_path


def _cmd_analyze(args: argparse.Namespace) -> int:
    text = _decode(Path(args.export).read_bytes())
    analyses = analyze_export(text)
    if not analyses:
        print("Aucun objet Report trouvé dans le fichier.", file=sys.stderr)
        return 1
    if args.json:
        payload = {"summary": summarize(analyses), "reports": [a.to_dict() for a in analyses]}
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Analyse écrite dans {args.json}")
    header = f"{'ID':>8}  {'Nom':<32} {'Layout':<12} {'Complexité':<14} {'Err':>3} {'Avt':>3}  Stratégie"
    print(header)
    print("-" * len(header))
    for a in analyses:
        errors = sum(1 for i in a.issues if i.severity == "error")
        warnings = sum(1 for i in a.issues if i.severity == "warning")
        layouts = "+".join(a.layouts) or "aucun"
        print(f"{a.report_id:>8}  {a.name[:32]:<32} {layouts:<12} "
              f"{a.complexity_label + ' ' + str(a.complexity_score):<14} {errors:>3} {warnings:>3}  {a.strategy['label']}")
        if args.verbose:
            for i in a.issues:
                print(f"{'':>10}[{i.severity}] {i.code}: {i.message}")
    s = summarize(analyses)
    print(f"\n{s['report_count']} rapport(s) · {s['error_count']} erreur(s) · {s['warning_count']} avertissement(s)")
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    try:
        path = compute_path(args.source, args.target, not args.onprem)
    except UnsupportedVersion as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"{path['source']} → {path['target']} : {path['summary']}")
    for h in path["hops"]:
        flag = {"documented": "documenté", "to_verify": "À VÉRIFIER", "factory_recommendation": "recommandation"}
        print(f"  {h['order']}. {h['from_version']} → {h['to_version']} "
              f"[{h['requirement']}, {flag[h['evidence']]}] {h['description']}")
        if h["documentation_url"]:
            print(f"     source : {h['documentation_url']}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nav2bc", description="NAV2BC Migration Factory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze-reports", help="Analyser les rapports d'un export TXT C/AL")
    p.add_argument("export", help="Fichier .txt exporté depuis C/SIDE (un ou plusieurs objets)")
    p.add_argument("--json", help="Écrire l'analyse complète dans ce fichier JSON")
    p.add_argument("-v", "--verbose", action="store_true", help="Afficher les anomalies")
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("path", help="Calculer le chemin de mise à niveau")
    p.add_argument("source", help="ex. NAV2016, NAV2009R2, BC14")
    p.add_argument("target", type=int, help="version BC cible, ex. 29")
    p.add_argument("--onprem", action="store_true", help="cible on-premises (par défaut : Online)")
    p.set_defaults(func=_cmd_path)

    p = sub.add_parser("serve", help="Démarrer l'API et l'interface web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db", default="data/nav2bc.db")
    p.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

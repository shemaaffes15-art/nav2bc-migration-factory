from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nav2bc.api.app import create_app
from nav2bc.knowledge.signature import normalize, signature_key
from nav2bc.knowledge.store import Store, ValidationError
from nav2bc.upgrade_path import UnsupportedVersion, compute_path

FIX = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------ signatures
def test_signature_ignores_variable_parts_but_keeps_error_codes():
    a = "AL0185: Table 'Vendor Ledger Entry Buffer' is missing (12,8)"
    b = "AL0185: Table 'Item Buffer' is missing (40,2)"
    assert normalize(a) == normalize(b) == "AL0185: Table '<NAME>' is missing (<POS>)"
    assert normalize("l'objet n'existe pas") == "l'objet n'existe pas"
    assert signature_key("code", a) == signature_key("code", b)
    assert signature_key("code", a) != signature_key("reports", a)


# ------------------------------------------------------------------ knowledge base
def test_documented_entry_requires_a_source():
    s = Store()
    with pytest.raises(ValidationError):
        s.add_entry({"change_type": "renamed", "description": "x", "evidence": "documented"})


def test_hypothesis_is_never_auto_applicable_until_validated():
    s = Store()
    e = s.add_entry({"change_type": "moved", "description": "Code tarif déplacé", "source_table": "Customer"})
    assert e["evidence"] == "hypothesis" and not e["auto_applicable"]
    with pytest.raises(ValidationError):
        s.set_entry_status(e["id"], "validated")  # validation must be nominative
    e = s.set_entry_status(e["id"], "validated", by="VN")
    assert not e["auto_applicable"]  # still a hypothesis
    e = s.update_entry(e["id"], {"evidence": "consultant"})
    assert e["validation_status"] == "proposed" and e["version"] == 2  # content change → back to review
    e = s.set_entry_status(e["id"], "validated", by="VN")
    assert e["auto_applicable"]
    assert [h["version"] for h in e["history"]] == [1]


def test_error_learning_loop():
    s = Store()
    p1 = s.create_project("P1", "NAV2016", 29)["id"]
    p2 = s.create_project("P2", "NAV2018", 29)["id"]
    first = s.record_error("code", "AL0185: Table 'A' is missing", p1)
    assert not first["known"]
    s.set_solution(first["id"], "Remplacer la table supprimée par son équivalent documenté", "Table supprimée")
    again = s.record_error("code", "AL0185: Table 'B' is missing", p2)
    assert again["id"] == first["id"] and again["known"]
    assert again["occurrences"] == 2 and again["project_count"] == 2
    assert again["confidence"] == 0.5
    s.feedback(first["id"], True)
    assert s.feedback(first["id"], True)["confidence"] == 0.75


# ------------------------------------------------------------------ upgrade path
def test_nav2016_to_online_has_three_upgrades_and_cloud_migration():
    p = compute_path("NAV2016", 29, online=True)
    assert [h["to_version"] for h in p["hops"]] == ["BC 14", "BC 25", "BC 29", "BC Online 29"]
    assert p["upgrade_count"] == 3 and p["to_verify"] == []
    assert p["hops"][2]["requirement"] == "recommended"


def test_nav2009_hop_is_flagged_to_verify():
    p = compute_path("NAV2009R2", 29)
    assert p["hops"][0]["to_version"] == "NAV 2013" and p["to_verify"] == [1]


def test_bc_sources_and_invalid_versions():
    assert [h["to_version"] for h in compute_path("BC23", 29, online=False)["hops"]] == ["BC 25", "BC 29"]
    assert [h["to_version"] for h in compute_path("BC26", 29)["hops"]] == ["BC 29", "BC Online 29"]
    with pytest.raises(UnsupportedVersion):
        compute_path("NAV5", 29)
    with pytest.raises(UnsupportedVersion):
        compute_path("BC25", 20)


# ------------------------------------------------------------------ API
@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "test.db")))


def test_api_end_to_end(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.post("/api/projects", json={"name": "Médina", "source_version": "NAV2016", "target_version": 29})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["upgrade_path"]["upgrade_count"] == 3

    with open(FIX / "export_custom_reports.txt", "rb") as fh:
        r = client.post(f"/api/projects/{pid}/reports", files={"file": ("export.txt", fh, "text/plain")})
    assert r.status_code == 200 and sorted(r.json()["imported"]) == [111, 50105]

    listing = client.get(f"/api/projects/{pid}/reports").json()
    assert listing["summary"]["report_count"] == 2
    detail = client.get(f"/api/projects/{pid}/reports/50105").json()
    assert detail["strategy"]["status"] == "proposition"

    # report errors were fed to the learning loop
    errors = client.get("/api/errors", params={"module": "reports"}).json()
    assert any("VAR-DOTNET" in e["normalized"] for e in errors)

    assert client.get("/").status_code == 200


def test_api_rejects_bad_input(client):
    assert client.post("/api/projects", json={"name": "X", "source_version": "Navision 3", "target_version": 29}).status_code == 422
    r = client.post("/api/projects", json={"name": "X", "source_version": "NAV2016", "target_version": 29})
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/reports", files={"file": ("x.txt", b"not an export", "text/plain")})
    assert r.status_code == 422
    assert client.get(f"/api/projects/{pid}/reports/1").status_code == 404
    r = client.post("/api/knowledge/entries", json={"change_type": "renamed", "description": "x", "evidence": "documented"})
    assert r.status_code == 422


def test_api_ansi_export(client):
    pid = client.post("/api/projects", json={"name": "X", "source_version": "NAV2016", "target_version": 29}).json()["id"]
    raw = (FIX / "report_206_sales_invoice.txt").read_text(encoding="utf-8").replace("Sales - Invoice", "Facture été")
    r = client.post(f"/api/projects/{pid}/reports", files={"file": ("x.txt", raw.encode("cp1252"), "text/plain")})
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}/reports/206").json()["name"] == "Facture été"

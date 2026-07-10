from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = ROOT / "bworkflow_sql"
FORBIDDEN = (
    "/api/workspaces",
    "/api/sourcing/categories",
    "/summary",
    "MasterDataService",
    "master_data",
)


def test_production_has_no_raw_master_endpoint_or_legacy_client_reference():
    findings = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in source:
                findings.append(f"{path.relative_to(ROOT)}: {token}")

    assert findings == []

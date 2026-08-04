"""API smoke tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.core.config import Settings


def test_upload_schema_and_common_query(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))

    # Rebuild settings pickup via env for store/ingest
    import sand.core.config as config_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: Settings(data_dir=data_dir))

    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["East", "West"], "amount": [10, 20]}).to_csv(csv_path, index=False)

    client = TestClient(app)
    with csv_path.open("rb") as fh:
        res = client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dataset_id"] == "sales"
    assert body["tables"]

    schema = client.get("/datasets/sales/schema")
    assert schema.status_code == 200
    assert "sales" in schema.json()["schema"]

    common = client.post(
        "/query/common",
        json={"dataset_id": "sales", "action": "groupby", "table": "sales", "params": {"group_by": ["region"], "metric": "amount", "agg": "sum"}},
    )
    assert common.status_code == 200, common.text
    assert common.json()["row_count"] == 2

    health = client.get("/health")
    assert health.json()["status"] == "ok"

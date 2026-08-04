"""CLI smoke: ingest/query/export/rename help text and basic flows."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sand.cli import build_parser, main


def test_cli_subcommands_and_no_xls() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    for name in ("serve", "ingest", "join", "query", "export", "import", "rename", "list"):
        assert name in help_text
    ingest_parser = next(
        action.choices["ingest"]
        for action in parser._actions  # noqa: SLF001
        if getattr(action, "choices", None) and "ingest" in action.choices
    )
    ingest_help = ingest_parser.format_help()
    assert "CSV / XLSX / Parquet" in ingest_help
    assert "XLS" not in ingest_help.replace("XLSX", "")
    join_parser = next(
        action.choices["join"]
        for action in parser._actions  # noqa: SLF001
        if getattr(action, "choices", None) and "join" in action.choices
    )
    join_help = join_parser.format_help()
    assert "--recipe" in join_help
    assert "--plan" in join_help


def test_cli_ingest_query_export_rename(tmp_path: Path, monkeypatch) -> None:
    import sand.core.config as config_mod
    from sand.core.config import Settings

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, llm_api_key="")
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)

    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["E", "W"], "amount": [10, 20]}).to_csv(csv_path, index=False)

    with pytest.raises(SystemExit) as ex:
        main(["ingest", str(csv_path), "--dataset", "shop", "--replace"])
    assert ex.value.code == 0

    with pytest.raises(SystemExit) as ex:
        main(["query", "--dataset", "shop", "--sql", "SELECT region, amount FROM sales", "--preview", "5"])
    assert ex.value.code == 0

    out_csv = tmp_path / "out.csv"
    with pytest.raises(SystemExit) as ex:
        main(["export", "--dataset", "shop", "--table", "sales", "--out", str(out_csv)])
    assert ex.value.code == 0
    assert out_csv.exists()

    with pytest.raises(SystemExit) as ex:
        main(["rename", "--dataset", "shop", "--new-id", "store"])
    assert ex.value.code == 0
    assert (data_dir / "store.duckdb").exists()
    assert not (data_dir / "shop.duckdb").exists()

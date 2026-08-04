"""CLI entry points: ``sand serve``, ``sand ingest``, ``sand join``, ``sand query``, …"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    from sand.core.config import get_settings

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port

    public_bind = host in {"0.0.0.0", "::", "[::]"}
    token = (settings.api_token or "").strip()
    allow_insecure = settings.allow_insecure_bind or os.environ.get("SAND_ALLOW_INSECURE_BIND", "").strip() in {
        "1",
        "true",
        "yes",
    }
    if public_bind and not token and not allow_insecure:
        print(
            "Refusing to bind "
            f"{host}:{port} without SAND_API_TOKEN.\n"
            "Compose publishes 127.0.0.1 by default. For a public bind, set SAND_API_TOKEN, "
            "or set SAND_ALLOW_INSECURE_BIND=1 to override (not recommended).",
            file=sys.stderr,
        )
        return 2

    uvicorn.run("sand.api.app:app", host=host, port=port, reload=args.reload)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.ingest.loader import ingest_files

    settings = get_settings()
    paths = [Path(p) for p in args.paths]
    for path in paths:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            return 1

    dataset_id = args.dataset or paths[0].stem
    table_names = args.table if args.table else None
    if table_names and len(table_names) not in {1, len(paths)}:
        print("--table must be one name or one per file", file=sys.stderr)
        return 1
    if table_names and len(table_names) == 1 and len(paths) > 1:
        table_names = None  # ignore single --table for multi-file; use stems

    sheets = args.sheets
    result = ingest_files(
        paths,
        dataset_id=dataset_id,
        db_path=settings.db_path(dataset_id),
        table_names=table_names if table_names and len(table_names) == len(paths) else None,
        if_exists="replace" if args.replace else "fail",
        xlsx_sheets=sheets,
    )
    print(f"Ingested {len(result.source_files)} file(s) into dataset '{result.dataset_id}' at {result.db_path}")
    for table in result.tables:
        print(f"  - {table.name}: {table.row_count} rows, {len(table.columns)} columns ({table.source_file})")
    return 0


def cmd_join(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.core.dataset_meta import get_recipe
    from sand.db.pool import DatabaseLockedError, close_client, get_client
    from sand.queries.joins import JoinKey, JoinPlan, JoinSpec, execute_join, execute_join_plan

    settings = get_settings()
    db_path = settings.db_path(args.dataset)
    if not db_path.exists():
        print(f"Dataset not found: {args.dataset}", file=sys.stderr)
        return 1

    plan: JoinPlan | None = None
    spec: JoinSpec | None = None
    as_table = args.as_table

    if args.plan:
        plan = JoinPlan.model_validate(json.loads(Path(args.plan).read_text(encoding="utf-8")))
        if as_table and not plan.as_table:
            plan = plan.model_copy(update={"as_table": as_table})
        as_table = plan.as_table
    elif args.recipe:
        peek = get_client(db_path, read_only=True)
        try:
            recipe = get_recipe(peek, args.recipe)
        finally:
            if peek.owns_connection:
                peek.close()
            else:
                close_client(db_path)
        if recipe is None:
            print(f"Unknown recipe: {args.recipe}", file=sys.stderr)
            return 1
        if recipe.plan is not None:
            plan = recipe.plan
            if as_table and not plan.as_table:
                plan = plan.model_copy(update={"as_table": as_table})
            as_table = plan.as_table
        else:
            spec = recipe.spec
            if spec is None:
                print(f"Recipe has no join spec: {args.recipe}", file=sys.stderr)
                return 1
            if as_table:
                spec = spec.model_copy(update={"as_table": as_table})
            as_table = spec.as_table
    else:
        if not args.left or not args.right or not args.on:
            print("Provide --left/--right/--on, or --recipe, or --plan", file=sys.stderr)
            return 1
        on: list[str | JoinKey] = list(args.on)
        spec = JoinSpec(
            left=args.left,
            right=args.right,
            on=on,
            how=args.how,
            as_table=as_table,
            limit=args.limit,
        )

    try:
        client = get_client(db_path, read_only=not bool(as_table))
    except DatabaseLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        if plan is not None:
            df, sql = execute_join_plan(client, plan)
        else:
            assert spec is not None
            df, sql = execute_join(client, spec)
        if as_table:
            client.checkpoint()
    finally:
        if client.owns_connection:
            client.close()
        else:
            close_client(db_path)
    print(sql)
    print(f"Rows: {len(df)}")
    if as_table:
        print(f"Materialized as table: {as_table}")
    if args.preview:
        print(df.head(args.preview).to_string(index=False))
    if args.json:
        print(json.dumps(df.head(args.preview or 20).to_dict(orient="records"), default=str))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.core.store import DatasetStore

    store = DatasetStore(get_settings())
    datasets = store.list_datasets()
    if not datasets:
        print("No datasets found.")
        return 0
    for d in datasets:
        tables = ", ".join(d.tables) if d.tables else "(none)"
        print(f"{d.id}\t{d.db_path}\t{tables}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.db.pool import DatabaseLockedError, close_client, get_client
    from sand.llm.nlsql import EVAL_LIMIT, assert_readonly_sql, with_eval_limit

    settings = get_settings()
    db_path = settings.db_path(args.dataset)
    if not db_path.exists():
        print(f"Dataset not found: {args.dataset}", file=sys.stderr)
        return 1
    sql_text = args.sql
    if args.file:
        sql_text = Path(args.file).read_text(encoding="utf-8")
    if not sql_text or not sql_text.strip():
        print("Provide --sql or --file", file=sys.stderr)
        return 1
    try:
        client = get_client(db_path, read_only=True)
    except DatabaseLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        sql = assert_readonly_sql(sql_text, allowed_tables=client.table_names())
        run_sql = sql if args.full else with_eval_limit(sql, EVAL_LIMIT)
        df = client.to_dataframe(run_sql)
    finally:
        if client.owns_connection:
            client.close()
        else:
            close_client(db_path)
    print(run_sql)
    print(f"Rows: {len(df)}")
    limit = args.preview if args.preview is not None else (None if args.full else 20)
    if limit is not None:
        print(df.head(limit).to_string(index=False))
    if args.json:
        print(json.dumps(df.head(limit or 20).to_dict(orient="records"), default=str))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.core.limits import guard_result_rows, limits_from_settings
    from sand.db.pool import DatabaseLockedError, close_client, get_client
    from sand.llm.nlsql import assert_readonly_sql

    settings = get_settings()
    db_path = settings.db_path(args.dataset)
    if not db_path.exists():
        print(f"Dataset not found: {args.dataset}", file=sys.stderr)
        return 1
    out = Path(args.out)
    fmt = args.format or out.suffix.lstrip(".").lower()
    if fmt == "duckdb":
        fmt = "db"
    if fmt not in {"csv", "xlsx", "parquet", "db"}:
        print("format must be csv, xlsx, parquet, or db", file=sys.stderr)
        return 1

    if fmt == "db":
        from sand.core.store import DatasetStore

        raw = DatasetStore(settings).export_bytes(args.dataset)
        out.write_bytes(raw)
        print(f"Wrote {out} ({len(raw):,} bytes)")
        return 0

    try:
        client = get_client(db_path, read_only=True)
    except DatabaseLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        if args.sql:
            sql = assert_readonly_sql(args.sql, allowed_tables=client.table_names())
        elif args.table:
            if args.table not in client.table_names():
                print(f"Unknown table: {args.table}", file=sys.stderr)
                return 1
            sql = f'SELECT * FROM "{args.table}"'
        else:
            print("Provide --table or --sql", file=sys.stderr)
            return 1
        limits = limits_from_settings(settings)
        guard_result_rows(client, sql, max_rows=limits.max_export_rows, action="export")
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            client.copy_to_csv(sql, out)
        elif fmt == "parquet":
            client.copy_to_parquet(sql, out)
        else:
            client.copy_to_xlsx(sql, out)
    finally:
        if client.owns_connection:
            client.close()
        else:
            close_client(db_path)
    print(f"Wrote {out}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.core.store import DatasetStore

    path = Path(args.path)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    dataset_id = args.dataset or path.stem
    settings = get_settings()
    dest = DatasetStore(settings).import_duckdb(path, dataset_id)
    print(f"Imported as dataset '{dest.stem}' at {dest}")
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.core.store import DatasetStore

    path = DatasetStore(get_settings()).rename(args.dataset, args.new_id)
    print(f"Renamed '{args.dataset}' → '{path.stem}' ({path})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sand", description="Spreadsheets Are Not Databases")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the localhost FastAPI server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    ingest = sub.add_parser("ingest", help="Load one or more spreadsheets into DuckDB")
    ingest.add_argument("paths", nargs="+", help="Paths to CSV / XLSX / Parquet files")
    ingest.add_argument("--dataset", default=None, help="Dataset id (default: first file stem)")
    ingest.add_argument(
        "--table",
        action="append",
        default=None,
        help="Optional table name(s); pass once per file to override stems",
    )
    ingest.add_argument(
        "--sheets",
        action="append",
        default=None,
        help="XLSX sheet name(s) to ingest (repeatable); default: all sheets",
    )
    ingest.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing tables with the same name (default: fail on conflict)",
    )
    ingest.set_defaults(func=cmd_ingest)

    join = sub.add_parser("join", help="Join two tables in a dataset")
    join.add_argument("--dataset", required=True, help="Dataset id")
    join.add_argument("--left", default=None, help="Left table name")
    join.add_argument("--right", default=None, help="Right table name")
    join.add_argument(
        "--on",
        action="append",
        default=None,
        help="Join key: shared name, or left=right (repeat for composite keys)",
    )
    join.add_argument("--how", default="inner", choices=["inner", "left", "right", "full"])
    join.add_argument("--as-table", dest="as_table", default=None, help="Save join as a new table")
    join.add_argument("--recipe", default=None, help="Run a saved join recipe by name")
    join.add_argument("--plan", default=None, help="Path to a JoinPlan JSON file")
    join.add_argument("--limit", type=int, default=None)
    join.add_argument("--preview", type=int, default=10, help="Print N preview rows")
    join.add_argument("--json", action="store_true", help="Also print preview as JSON")
    join.set_defaults(func=cmd_join)

    listing = sub.add_parser("list", help="List datasets and their tables")
    listing.set_defaults(func=cmd_list)

    query = sub.add_parser("query", help="Run read-only SQL against a dataset (no LLM)")
    query.add_argument("--dataset", required=True)
    query.add_argument("--sql", default=None, help="SQL string")
    query.add_argument("--file", default=None, help="Path to a .sql file")
    query.add_argument("--full", action="store_true", help="Skip preview LIMIT wrapper")
    query.add_argument("--preview", type=int, default=None, help="Print N preview rows")
    query.add_argument("--json", action="store_true")
    query.set_defaults(func=cmd_query)

    export = sub.add_parser("export", help="Export a table/SQL result or whole .duckdb")
    export.add_argument("--dataset", required=True)
    export.add_argument("--out", required=True, help="Output path")
    export.add_argument("--format", choices=["csv", "xlsx", "parquet", "db"], default=None)
    export.add_argument("--table", default=None)
    export.add_argument("--sql", default=None)
    export.set_defaults(func=cmd_export)

    imp = sub.add_parser("import", help="Import an existing .duckdb file as a dataset")
    imp.add_argument("path", help="Path to .duckdb file")
    imp.add_argument("--dataset", default=None, help="Dataset id (default: file stem)")
    imp.set_defaults(func=cmd_import)

    rename = sub.add_parser("rename", help="Rename a dataset id")
    rename.add_argument("--dataset", required=True, help="Current dataset id")
    rename.add_argument("--new-id", required=True, dest="new_id", help="New dataset id")
    rename.set_defaults(func=cmd_rename)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

"""CLI entry points: ``sand serve``, ``sand ingest``, ``sand join``."""

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

    result = ingest_files(
        paths,
        dataset_id=dataset_id,
        db_path=settings.db_path(dataset_id),
        table_names=table_names if table_names and len(table_names) == len(paths) else None,
        if_exists="replace" if args.replace else "fail",
    )
    print(f"Ingested {len(result.source_files)} file(s) into dataset '{result.dataset_id}' at {result.db_path}")
    for table in result.tables:
        print(f"  - {table.name}: {table.row_count} rows, {len(table.columns)} columns ({table.source_file})")
    return 0


def cmd_join(args: argparse.Namespace) -> int:
    from sand.core.config import get_settings
    from sand.db.pool import DatabaseLockedError, close_client, get_client
    from sand.queries.joins import JoinKey, JoinSpec, execute_join

    settings = get_settings()
    db_path = settings.db_path(args.dataset)
    if not db_path.exists():
        print(f"Dataset not found: {args.dataset}", file=sys.stderr)
        return 1

    on: list[str | JoinKey] = list(args.on)
    spec = JoinSpec(
        left=args.left,
        right=args.right,
        on=on,
        how=args.how,
        as_table=args.as_table,
        limit=args.limit,
    )
    try:
        client = get_client(db_path, read_only=False)
    except DatabaseLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        df, sql = execute_join(client, spec)
        if args.as_table:
            client.checkpoint()
    finally:
        # CLI is one-shot — release so `sand serve` can reopen
        close_client(db_path)
    print(sql)
    print(f"Rows: {len(df)}")
    if args.as_table:
        print(f"Materialized as table: {args.as_table}")
    if args.preview:
        print(df.head(args.preview).to_string(index=False))
    if args.json:
        print(json.dumps(df.head(args.preview or 20).to_dict(orient="records"), default=str))
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
    ingest.add_argument("paths", nargs="+", help="Paths to CSV/XLSX/XLS/Parquet files")
    ingest.add_argument("--dataset", default=None, help="Dataset id (default: first file stem)")
    ingest.add_argument(
        "--table",
        action="append",
        default=None,
        help="Optional table name(s); pass once per file to override stems",
    )
    ingest.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing tables with the same name (default: fail on conflict)",
    )
    ingest.set_defaults(func=cmd_ingest)

    join = sub.add_parser("join", help="Join two tables in a dataset")
    join.add_argument("--dataset", required=True, help="Dataset id")
    join.add_argument("--left", required=True, help="Left table name")
    join.add_argument("--right", required=True, help="Right table name")
    join.add_argument(
        "--on",
        action="append",
        required=True,
        help="Join key: shared name, or left=right (repeat for composite keys)",
    )
    join.add_argument("--how", default="inner", choices=["inner", "left", "right", "full"])
    join.add_argument("--as-table", dest="as_table", default=None, help="Save join as a new table")
    join.add_argument("--limit", type=int, default=None)
    join.add_argument("--preview", type=int, default=10, help="Print N preview rows")
    join.add_argument("--json", action="store_true", help="Also print preview as JSON")
    join.set_defaults(func=cmd_join)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

# Spreadsheets Are Not Databases (SAND)

## Problem
Spreadsheets shine at presenting data. Databases are better for storing, analyzing, and querying it. Teams still often use huge spreadsheets as makeshift databases.

## Solution
SAND turns spreadsheets into a **DuckDB** database, exposes a localhost API + web UI, and provides Jupyter helpers. Load multiple files, join them with explicit keys, ask questions in natural language, and get **chart-first** answers.

## Quickstart

```bash
./scripts/bootstrap.sh
source .sand-venv/bin/activate

# Load one or more spreadsheets into the same dataset (CSV / XLSX / Parquet)
sand ingest sales.csv customers.csv --dataset shop
# Optional: limit XLSX sheets
# sand ingest book.xlsx --dataset book --sheets Sheet1 --sheets Sheet2

# Join with an explicit key mapping (shared name, or left=right)
sand join --dataset shop --left sales --right customers --on cust_id=id --how left --as-table sales_enriched

# Read-only SQL (no LLM), export, or rename
sand query --dataset shop --sql "SELECT * FROM sales_enriched LIMIT 20"
sand export --dataset shop --table sales_enriched --out out.csv
# sand rename --dataset shop --new-id store

# Start API + chat UI (OpenAPI at /docs)
sand serve
# → http://127.0.0.1:8765
```

`scripts/bootstrap.sh` creates `.sand-venv` (the only supported local venv), installs `sand[dev]`, and preloads the DuckDB excel extension. Override with `SAND_PYTHON=/path/to/python` or `SAND_VENV=...` if needed.

### LLM config (for chat)

Copy `.env.example` to `.env` and set variables, or export directly:

```bash
export SAND_LLM_API_KEY=sk-...
export SAND_LLM_BASE_URL=https://api.openai.com/v1   # or Ollama: http://127.0.0.1:11434/v1
export SAND_LLM_MODEL=gpt-4o-mini
```

### Docker

```bash
docker compose up --build
# → http://127.0.0.1:8765  (published on localhost only)
```

Remote publish requires a token:

```bash
SAND_API_TOKEN=secret docker compose up --build
# then call APIs with: Authorization: Bearer secret
```

Image builds install pinned deps from `requirements.txt`, then the local package with `--no-deps`.

### Jupyter

```python
from sand.jupyter import load

ds = load(["sales.csv", "customers.csv"], dataset_id="shop")
ds.add("products.csv")
ds.join("sales", "customers", on="cust_id=id", how="left", as_table="enriched")
ds.profile("enriched")
ds.chart(sql="SELECT region, SUM(amount) AS total FROM enriched GROUP BY region")
```

## Website workflow (`sand serve`)

1. **Data** — multi-upload (CSV/XLSX/Parquet), Excel sheet picker, import `.duckdb`, load sample shop, schema browser (profile + lineage), review/apply column types, rename/drop tables, export CSV/XLSX/Parquet/DuckDB, duplicate/delete datasets
2. **Join** — auto-suggested keys, multi-step join plans, row-count estimate + many-to-many warnings, saved join recipes (run/delete), preview + export
3. **SQL** — run DuckDB SQL directly (no LLM), preview/full run, chart + export
4. **Chat & charts** — conversation memory per dataset, editable/rerunnable SQL, filter + offline asks, always evaluates with `LIMIT 10` first, then optional full run. Charts use a **locally vendored** Plotly.js (refreshed from the CDN when online).

UI state (active dataset + tab) persists in `localStorage` across refreshes.

## API (selected)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/datasets` | List datasets — returns `{datasets, orphans, empty, hint}` (not a bare array) |
| DELETE | `/datasets/orphans/{stem}` | Delete a legacy SQLite `*.db` leftover |
| POST | `/datasets/upload` | Ingest one/many CSV / XLSX / Parquet files (optional `sheets` for XLSX) |
| POST | `/datasets/xlsx/sheets` | List sheet names in an uploaded XLSX |
| POST | `/datasets/import` | Import an existing `.duckdb` file |
| POST | `/datasets/samples/shop` | Load demo dataset |
| GET | `/datasets/{id}/schema` | Schema + table `lineage` (source file, sheet, row count) |
| GET | `/datasets/{id}/profile/{table}` | Column profile + samples |
| GET/POST | `/datasets/{id}/types/{table}` | Infer / apply Pydantic-validated types |
| POST | `/query/sql` | Run read-only SQL without an LLM (preview/full + chart) |
| POST | `/query/join/suggest` | Join key suggestions |
| POST | `/query/join/estimate` | Cardinality + fan-out warning |
| POST | `/query/join` | Run join or multi-step `plan` (+ optional recipe save) |
| GET/POST/DELETE | `/query/join/recipes...` | Persist join recipes |
| POST | `/chat` | NL→SQL (preview `LIMIT 10` first); 503 if no LLM |
| POST | `/chat/cancel` | Interrupt in-flight DuckDB query (best-effort) |
| POST | `/chat/common-ask` | Offline asks (no LLM): profile / missing / top_n / groupby / time_series / filter |
| GET/POST/DELETE | `/chat/views...` | Saved SQL views (optional cache + over-cap charts) |
| GET/DELETE | `/chat/{id}/history` | Chat memory |
| POST | `/export/{csv\|xlsx\|parquet\|db}` | Export table/SQL result or whole `.duckdb` file |

Datasets are stored under `.sand/data/<id>.duckdb`. Chat history is a sidecar `.sand/data/<id>.chat.jsonl` so asks can open DuckDB read-only. Dataset ids are sanitized to `[A-Za-z0-9_-]` (max 64); path separators / `..` are rejected. Uploads are size-capped (`SAND_MAX_INGEST_BYTES`, default 200 MB) while streaming to disk. Join materialize / export / full chat results are row-capped (`SAND_MAX_*_ROWS`). Offline asks (`top_n` / `groupby` / `filter`) are capped by `SAND_MAX_OFFLINE_ASK_ROWS` (default 10 000).

### Error responses

API errors use FastAPI's `{"detail": ...}` where `detail` is usually an object:

```json
{"detail": {"code": "locked", "message": "..."}}
```

| HTTP | `code` | Meaning |
|------|--------|---------|
| 400 | `bad_request` | Validation / bad params |
| 404 | `not_found` | Missing dataset/orphan |
| 409 | `conflict` | Dataset already exists |
| 410 | `deprecated` | e.g. `/query/common` `action=join` — use `/query/join` |
| 413 | `limit_exceeded` | File size or row guard |
| 423 | `locked` | DuckDB file lock |
| 502 | `llm_upstream` | LLM HTTP error |
| 503 | `llm_not_configured` / `llm_unreachable` | No key or endpoint down (`offline_actions` listed) |

Tabular endpoints (`/query/common`, `/chat/common-ask`, `/query/join`) share `{dataset_id, columns, rows, row_count, ...}`.

## Why DuckDB

SAND’s core path is analytical: ingest wide sheets, join facts to dimensions, aggregate, chart. DuckDB is a columnar, vectorized, parallel OLAP engine built for that shape — faster scans/joins/group-bys than a row store for spreadsheet-warehouse workloads, while still embedding in-process with a single file per dataset.

Postgres remains a longer-term option for multi-user hosting. The `DatabaseClient` protocol keeps the door open for other backends.

## Layout

```
src/sand/
  api/           # FastAPI app + routes/
  core/          # config, limits, store, SQL scan, dataset metadata
  db/            # DuckDB client + connection pool
  ingest/        # spreadsheet loaders
  queries/       # common asks, joins, predicates
  charts/ llm/ jupyter/ samples/ web/ (web/js modules)
scripts/bootstrap.sh
tests/
```

## Versioning

Current release: **0.9.1** — see [CHANGELOG.md](CHANGELOG.md)

- Patch `0.0.1` — small fixes, hardening, refactors that do not change API/behavior contracts
- Minor `0.1.0` — new features, or changes that break callers/API/CLI
- Major `1.0.0` — complete redesign / stability commitment

## Dependencies & lockfiles

- **Source of truth for declared deps:** `pyproject.toml`
- **Pinned install set for Docker/CI:** `requirements.txt`
  (`uv pip compile pyproject.toml -o requirements.txt` or equivalent)
- **`uv.lock` is only for uv users** — if you use `uv` locally for resolves/sync, keep it; everyone else (pip, Docker, CI) should ignore it and use `requirements.txt`. Do not treat `uv.lock` as the project-wide lockfile.
- Dev tools (`ruff`, `mypy`, `pytest`) live in `.[dev]`

## Goals
1. Transfer spreadsheet → DuckDB (analytics-first)
2. Jupyter support
3. LLM natural-language queries (preview-first)
4. Chart-first answers + export to CSV / XLSX / Parquet / DuckDB

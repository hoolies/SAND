# Changelog

All notable changes to SAND are documented here.

## [0.9.3] — 2026-08-04

### Added
- Recipe **Load** into the Join form (edit multi-step plans without rebuilding)
- Upload/import **Cancel** (aborts in-flight XHR)
- Multi-step **JoinPlan** estimate (`POST /query/join/estimate` with `plan`)
- SQL tab table/column insert helpers
- CLI: `sand list`, `sand join --recipe` / `--plan`
- Join request optional `write` flag (explicit write; still inferred from `as_table` / recipe save)
- `/docs`, `/openapi.json`, `/redoc` reachable without API token

### Changed
- Multi-step joins run as **nested SQL** (no TEMP intermediates)
- README documents rename, rows peek, checkpoint, RO-by-default, CLI surface

## [0.9.2] — 2026-08-04

### Added
- Join cancel: track DuckDB queries + Cancel/Esc on the Join tab (same as SQL/Chat)

### Changed
- Connections default to **read-only**; write opens only when materializing (`as_table`), saving/deleting recipes/views, ingest, renames, checkpoints, type apply, etc.
- Multi-step join plans use TEMP tables so preview works without a write lock
- Running a saved recipe no longer re-writes the recipe row (save-on-run only when join/plan is sent with a name)
- API token check uses `hmac.compare_digest`

### Fixed
- Join-plan intermediate tables are dropped on failure (no leftover `tmp_join_*`)

## [0.9.1] — 2026-08-04

### Added
- Multi-step **JoinPlan** recipes (save/load/run via `/query/join/recipes` and `recipe_name`)
- CLI: `sand query`, `sand export`, `sand import`, `sand rename`; ingest `--sheets` for XLSX
- Data-tab row peek: `GET /datasets/{id}/rows/{table}`
- Dataset rename: `POST /datasets/{id}/rename` (+ UI)
- Keyboard shortcuts (1–4 tabs, Ctrl/Cmd+Enter run, Esc cancel) and tab ARIA roles
- Drag-and-drop upload zone; API docs link in the header
- Show `X-Request-ID` on UI errors; checkpoint confirm before destructive drop/delete

### Changed
- CLI ingest help lists CSV / XLSX / Parquet only (no legacy `.xls`)

## [0.9.0] — 2026-08-04

### Added
- **SQL tab** — run read-only DuckDB SQL without an LLM (`POST /query/sql`); AI remains optional
- Offline **Filter** ask in Chat (`/chat/common-ask` action=`filter`)
- Excel **sheet picker** before ingest (`POST /datasets/xlsx/sheets` + optional `sheets` form field)
- Editable last-SQL + Run/Copy in Chat (reruns via `/query/sql`)
- Persist active dataset + tab in `localStorage`
- Preview “Show more” pagination beyond 50 rows
- Upload progress percent for large files
- Locked-dataset warn + Retry when DuckDB is held by another process
- `/health.limits` exposes effective `SAND_*` caps
- Table **lineage** (source file / sheet / row count) on schema + Data tab
- **Parquet** export; **import** existing `.duckdb` (`POST /datasets/import`)
- Multi-step **JoinPlan** UI (“Add join step”)
- Recipe delete restored on the Join list
- Sample notebook `examples/sand_quickstart.ipynb`
- `.env.example`
- Shutdown auto-checkpoint for pooled datasets

### Changed
- Tabs: 1 Data → 2 Join → 3 SQL → 4 Chat
- Upload docs no longer mention legacy `.xls`

## [0.8.4] — 2026-08-04

### Added
- Vendored Plotly.js under `/static/vendor/plotly/`; runtime checks npm/CDN for newer **same-major** builds and upgrades when online, otherwise keeps the local bundle
- Health payload includes `plotly` status (`version`, `bundle_ready`, `online`, …)

### Changed
- Join recipe list is run-only in the UI (no Delete); optional recipe name is saved when you **Run join**
- Removed dedicated Save recipe button from the Join tab

## [0.8.3] — 2026-08-04

### Added
- Theme toggle: **Catppuccin Latte** (light) and **Tokyo Night Storm** (dark), persisted in `localStorage`
- Query cancel: `POST /chat/cancel` + Cancel button (AbortController + DuckDB `interrupt`)
- Saved chat views: name/SQL presets with optional result cache and optional chart sample over-cap (`/chat/views*`)
- Chat responses include `chart_sample_rows`, `chart_capped`, and `max_result_rows` for UI clarity
- This changelog

### Changed
- “Run full query” notice explains chart sample caps and points to CSV/XLSX export for complete data
- Regenerated `requirements.txt` (dropped stale `xlrd` pin after legacy `.xls` removal)

## [0.8.2] — 2026-08-03

### Added
- Frontend API token field (Bearer / `X-SAND-Token` from `localStorage`)
- Structured request logging with `X-Request-ID`
- Dataset disk usage / budget warning on `GET /datasets`
- `POST /datasets/{id}/checkpoint` (checkpoint + vacuum helper)
- One LLM SQL repair retry on guard/exec failure
- Chart sample row cap (`SAND_CHART_SAMPLE_ROWS`, default 5 000) for full chat runs
- Hard mypy gate in CI (`mypy src/sand`)

### Changed
- Web UI split into ES modules under `src/sand/web/js/`
- Join key suggestions fail closed (no pandas fallback)
- Dropped legacy `.xls` / `xlrd` support (CSV / XLSX / Parquet only)

## [0.8.1] — 2026-08

### Added
- DuckDB connection pool; CTAS ingest; structured filters
- NL→SQL allowlist hardening, LIMIT literal strip, outer eval wrap
- Query timeout via DuckDB interrupt
- CSV stream export; XLSX via CSV staging + openpyxl write-only
- Chat history sidecar `.sand/data/<id>.chat.jsonl` (DuckDB stays read-only on `/chat`)
- Disk budget guards; safer dataset snapshots
- Optional `SAND_API_TOKEN`; richer 422 error envelope
- Typed offline asks (`/chat/common-ask`)

### Changed
- Package layout under `src/sand/{api,core,db,ingest,queries,charts,llm,jupyter,samples,web}`
- Docker Excel extension preinstall; bootstrap via `scripts/bootstrap.sh`

## [0.8.0] — 2026-08

### Added
- Initial DuckDB-backed SAND: multi-file ingest, explicit joins, recipes, NL chat → charts
- FastAPI localhost API + web UI + Jupyter helpers
- Sample shop dataset, export CSV/XLSX/DuckDB
- Preview-first chat evaluation (`LIMIT 10`) before optional full run

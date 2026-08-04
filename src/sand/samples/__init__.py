"""Sample datasets packaged with SAND."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from sand.core.config import get_settings, sanitize_dataset_id
from sand.ingest.loader import ingest_files, ingest_result_payload

SAMPLES_DIR = Path(__file__).resolve().parent


def ensure_sample_files() -> list[Path]:
    """Write sample CSVs if missing; return paths."""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    sales = SAMPLES_DIR / "sales.csv"
    customers = SAMPLES_DIR / "customers.csv"
    products = SAMPLES_DIR / "products.csv"

    if not sales.exists():
        pd.DataFrame(
            {
                "order_id": [1, 2, 3, 4, 5, 6],
                "cust_id": [10, 20, 10, 30, 20, 10],
                "product_sku": ["A1", "B2", "A1", "C3", "B2", "C3"],
                "amount": [120.0, 80.5, 40.0, 200.0, 95.0, 150.0],
                "order_date": [
                    "2024-01-05",
                    "2024-01-18",
                    "2024-02-02",
                    "2024-02-20",
                    "2024-03-01",
                    "2024-03-15",
                ],
            }
        ).to_csv(sales, index=False)

    if not customers.exists():
        pd.DataFrame(
            {
                "id": [10, 20, 30],
                "name": ["Ada Lovelace", "Bob Builder", "Cara Cruz"],
                "region": ["East", "West", "East"],
            }
        ).to_csv(customers, index=False)

    if not products.exists():
        pd.DataFrame(
            {
                "sku": ["A1", "B2", "C3"],
                "product": ["Widget", "Gadget", "Doohickey"],
                "category": ["Hardware", "Hardware", "Software"],
            }
        ).to_csv(products, index=False)

    return [sales, customers, products]


def load_sample_shop(dataset_id: str = "shop") -> dict:
    paths = ensure_sample_files()
    settings = get_settings()
    ds_id = sanitize_dataset_id(dataset_id)
    result = ingest_files(paths, dataset_id=ds_id, db_path=settings.db_path(ds_id), if_exists="replace")
    return ingest_result_payload(
        result,
        suggested_joins=[
            {"left": "sales", "right": "customers", "on": [{"left": "cust_id", "right": "id"}]},
            {"left": "sales", "right": "products", "on": [{"left": "product_sku", "right": "sku"}]},
        ],
    )

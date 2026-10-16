"""Run from any directory: python /path/to/run_etl.py [--csv-only]."""

import argparse
from datetime import datetime, timezone
from getpass import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import uuid

from dotenv import load_dotenv
import numpy as np
import pandas as pd
from sqlalchemy import text

from retail_etl.analysis import audit_data_quality, build_analysis_tables
from retail_etl.cleaning import build_analysis_datasets
from retail_etl.extract import load_retail_data, validate_and_convert
from retail_etl.load import make_engine, publish_mysql

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)


def database_engine():
    password = os.getenv("MYSQL_PASSWORD")
    if password is None:
        password = getpass("MySQL root password (hidden): ")
    return make_engine(
        os.getenv("MYSQL_HOST", "127.0.0.1"), os.getenv("MYSQL_PORT", "3306"),
        os.getenv("MYSQL_USER", "root"), password,
        os.getenv("MYSQL_DATABASE", "online_retail_etl"),
    )


def verify_analysis(tables):
    products, customers = tables["product_sales"], tables["customer_sales"]
    if products.duplicated().any() or not products.SalesAmount.gt(0).all():
        raise ValueError("Product sales quality check failed")
    if customers.CustomerID.isna().any():
        raise ValueError("Customer sales has missing IDs")
    for name, column in (("monthly_sales", "sales"), ("country_sales", "sales")):
        if not np.isclose(tables[name][column].sum(), products.SalesAmount.sum(), rtol=0, atol=0.01):
            raise ValueError(f"Analysis reconciliation failed: {name}")
    for name, column in (("customer_summary", "total_amount"), ("customer_rfm", "monetary"), ("rfm_segments", "total_sales_amount")):
        if not np.isclose(tables[name][column].sum(), customers.SalesAmount.sum(), rtol=0, atol=0.01):
            raise ValueError(f"Customer reconciliation failed: {name}")
    if tables["rfm_segments"].customer_count.sum() != customers.CustomerID.nunique():
        raise ValueError("RFM customer reconciliation failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data.csv")
    parser.add_argument("--csv-only", action="store_true", help="Validate calculations and export without connecting to MySQL")
    parser.add_argument("--check-connection", action="store_true", help="Read-only connection check; no tables written")
    parser.add_argument("--exclude-month", action="append", default=None,
                        help="Month excluded from growth comparisons; repeatable. Default: 2011-12")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = None
    if not args.csv_only:
        engine = database_engine()
        try:
            with engine.connect() as connection:
                version, database = connection.execute(text("SELECT VERSION(), DATABASE()")).one()
                LOG.info("Connected to MySQL %s / %s", version, database)
        except Exception as error:
            LOG.error("MySQL connection failed (%s). Check local credentials and database.", type(error).__name__)
            engine.dispose()
            return 1
    if args.check_connection:
        if engine is None:
            parser.error("--check-connection cannot be combined with --csv-only")
        engine.dispose()
        return 0
    excluded_months = args.exclude_month if args.exclude_month is not None else ["2011-12"]
    for month in excluded_months:
        if str(pd.Period(month, freq="M")) != month:
            parser.error("--exclude-month requires YYYY-MM")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6]
    batch_dir = ROOT / "output" / run_id
    batch_dir.mkdir(parents=True)
    handler = logging.FileHandler(batch_dir / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    manifest = {
        "run_id": run_id, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running", "input": str(args.input.resolve()),
        "excluded_months": excluded_months, "mysql_published": False,
        "rules": {"exact_duplicate_removal": True, "product_grain": ["StockCode", "Description"],
                  "sales_are_return_adjusted": False, "money_storage": "DOUBLE; reconciliation tolerance GBP 0.01"},
    }
    try:
        with args.input.open("rb") as stream:
            before_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        raw = load_retail_data(args.input)
        with args.input.open("rb") as stream:
            after_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if before_hash != after_hash:
            raise ValueError("Source changed while reading; batch rejected")
        manifest["input_sha256"] = before_hash
        converted = validate_and_convert(raw)
        clean, candidates, products, customers = build_analysis_datasets(converted)
        tables = {
            "quality_audit": audit_data_quality(converted),
            "pipeline_counts": pd.DataFrame([
                {"step": key, "row_count": len(frame)} for key, frame in (
                    ("raw", raw), ("deduplicated", clean), ("sales_candidates", candidates),
                    ("product_sales", products), ("customer_sales", customers),
                )
            ]),
            "product_sales": products, "customer_sales": customers,
            **build_analysis_tables(products, customers, excluded_months),
        }
        verify_analysis(tables)
        # Store batch identity in every SQL row and matching CSV row.
        for frame in tables.values():
            frame.insert(0, "run_id", run_id)
        for name, frame in tables.items():
            destination = batch_dir / f"{name}.csv"
            frame.to_csv(destination, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d %H:%M:%S")
            # Read CSV back to verify exported rows and numeric totals.
            exported = pd.read_csv(destination, low_memory=False)
            if len(exported) != len(frame):
                raise ValueError(f"CSV row count mismatch: {name}")
            for column in ("SalesAmount", "sales", "total_amount", "monetary", "total_sales_amount"):
                if column in frame and not np.isclose(exported[column].sum(), frame[column].sum(), rtol=0, atol=0.01):
                    raise ValueError(f"CSV amount mismatch: {name}.{column}")
            LOG.info("CSV verified: %s (%s rows)", name, len(frame))
        manifest["row_counts"] = {name: len(frame) for name, frame in tables.items()}
        manifest["core_metrics"] = json.loads(tables["core_metrics"].to_json(orient="records"))[0]
        if engine is not None:
            publish_mysql(engine, tables, run_id)
            manifest["mysql_published"] = True
        manifest["status"] = "csv_only_complete" if args.csv_only else "complete"
        LOG.info("Batch %s: %s. Results: %s", run_id, manifest["status"], batch_dir)
        return 0
    except Exception as error:
        # Do not dump connection strings, SQL parameters or credentials to logs.
        manifest["status"] = "failed"
        manifest["error_type"] = type(error).__name__
        LOG.error("Batch failed (%s); inspect source/configuration. Previous published batch is retained unless publication already succeeded.", type(error).__name__)
        if isinstance(error, (ValueError, FileNotFoundError)):
            LOG.error("Validation detail: %s", error)
        return 1
    finally:
        manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        (batch_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if engine is not None:
            engine.dispose()
        logging.getLogger().removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())

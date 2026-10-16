"""Load a complete batch into staging tables, verify, then atomically publish."""

import logging

import numpy as np
import pandas as pd
from sqlalchemy import BigInteger, Boolean, DateTime, Float, String, create_engine, inspect, text
from sqlalchemy.engine import URL

LOG = logging.getLogger(__name__)
DATABASE = "online_retail_etl"


def make_engine(host, port, user, password, database):
    # Guard against accidentally pointing the loader at the old analysis DB.
    if database != DATABASE:
        raise ValueError(f"This project only writes to {DATABASE}")
    url = URL.create(
        "mysql+pymysql", username=user, password=password, host=host,
        port=int(port), database=database, query={"charset": "utf8mb4"},
    )
    return create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})


def sql_types(frame):
    result = {}
    for name, dtype in frame.dtypes.items():
        if pd.api.types.is_bool_dtype(dtype):
            result[name] = Boolean()
        elif pd.api.types.is_integer_dtype(dtype):
            result[name] = BigInteger()
        elif pd.api.types.is_float_dtype(dtype):
            result[name] = Float(precision=53)
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            result[name] = DateTime()
        else:
            longest = int(frame[name].dropna().astype(str).str.len().max()) if frame[name].notna().any() else 1
            result[name] = String(max(32, longest))
    return result


def publish_mysql(engine, tables, run_id):
    stage = {name: f"_stage_{run_id}_{name}" for name in tables}
    backups = []
    with engine.connect() as connection:
        locked = connection.execute(text("SELECT GET_LOCK('online_retail_etl_publish', 0)")).scalar()
        connection.commit()
        if locked != 1:
            raise RuntimeError("Another ETL batch is writing; retry after it finishes")
        published = False
        try:
            for name, frame in tables.items():
                LOG.info("Staging %s (%s rows)", name, len(frame))
                frame.to_sql(
                    stage[name], connection, if_exists="fail", index=False,
                    chunksize=2000, dtype=sql_types(frame),
                )
                connection.commit()
                count = connection.execute(text(f"SELECT COUNT(*) FROM `{stage[name]}`")).scalar()
                if count != len(frame):
                    raise ValueError(f"Row count mismatch: {name}")
            # Check full-detail totals, not just a copied metrics row.
            checks = (
                ("product_sales", "SalesAmount"), ("customer_sales", "SalesAmount"),
                ("customer_rfm", "monetary"), ("monthly_sales", "sales"),
                ("country_sales", "sales"), ("product_sales_summary", "sales"),
                ("customer_summary", "total_amount"), ("rfm_segments", "total_sales_amount"),
            )
            for name, column in checks:
                actual = connection.execute(text(f"SELECT SUM(`{column}`) FROM `{stage[name]}`")).scalar()
                if not np.isclose(float(actual), float(tables[name][column].sum()), rtol=0, atol=0.01):
                    raise ValueError(f"Sales amount mismatch: {name}")
            totals = connection.execute(text(
                f"SELECT COUNT(DISTINCT InvoiceNo), COUNT(DISTINCT CustomerID), SUM(Quantity) "
                f"FROM `{stage['product_sales']}`"
            )).one()
            expected = tables["core_metrics"].iloc[0]
            if tuple(map(int, totals)) != (int(expected.orders), int(expected.customers), int(expected.units)):
                raise ValueError("Database order/customer/unit reconciliation failed")
            existing = set(inspect(connection).get_table_names())
            renames = []
            for name in tables:
                if name in existing:
                    backup = f"_old_{run_id}_{name}"
                    backups.append(backup)
                    renames.append(f"`{name}` TO `{backup}`")
                renames.append(f"`{stage[name]}` TO `{name}`")
            connection.commit()
            connection.execute(text("RENAME TABLE " + ", ".join(renames)))
            connection.commit()
            published = True
            LOG.info("All MySQL tables published and reconciled")
            # A cleanup failure must not falsely report that publication failed.
            for name in backups:
                try:
                    connection.execute(text(f"DROP TABLE `{name}`"))
                    connection.commit()
                except Exception:
                    LOG.warning("Published successfully; old table needs cleanup: %s", name)
                    connection.rollback()
        finally:
            connection.rollback()
            if not published:
                for name in stage.values():
                    connection.execute(text(f"DROP TABLE IF EXISTS `{name}`"))
                connection.commit()
            connection.execute(text("SELECT RELEASE_LOCK('online_retail_etl_publish')"))
            connection.commit()

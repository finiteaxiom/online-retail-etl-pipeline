"""Read the source without changing it; reject invalid business keys/types."""

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = (
    "InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
    "UnitPrice", "CustomerID", "Country",
)


def validate_required_columns(frame: pd.DataFrame) -> None:
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")


def load_retail_data(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path, encoding="cp1252", low_memory=False,
        dtype={key: "string" for key in REQUIRED_COLUMNS},
    )
    validate_required_columns(frame)
    if frame.empty:
        raise ValueError("Source file is empty")
    # Keep only the eight source fields; metadata must not affect deduplication.
    return frame.loc[:, list(REQUIRED_COLUMNS)].copy()


def validate_and_convert(frame: pd.DataFrame) -> pd.DataFrame:
    validate_required_columns(frame)
    result = frame.copy()
    for key in ("InvoiceNo", "StockCode", "Country"):
        if (result[key].isna() | result[key].str.strip().eq("")).any():
            raise ValueError(f"Missing or blank business field: {key}")
    dates = pd.to_datetime(
        result["InvoiceDate"], format="%m/%d/%Y %H:%M", errors="coerce",
    )
    if dates.isna().any():
        raise ValueError(f"Invalid InvoiceDate rows: {int(dates.isna().sum())}")
    result["InvoiceDate"] = dates
    for key in ("Quantity", "UnitPrice", "CustomerID"):
        values = pd.to_numeric(result[key], errors="coerce")
        invalid = (result[key].notna() & values.isna()) | (values.notna() & ~np.isfinite(values))
        if key != "CustomerID":
            invalid |= values.isna()
        if key in ("Quantity", "CustomerID"):
            invalid |= values.notna() & values.mod(1).ne(0)
        if invalid.any():
            raise ValueError(f"Invalid {key} rows: {int(invalid.sum())}")
        result[key] = values.astype("Int64") if key != "UnitPrice" else values.astype(float)
    return result

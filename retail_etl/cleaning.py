"""Deterministic cleaning rules shared by the Notebook and SQL analysis."""

from collections.abc import Iterable

import pandas as pd


SPECIAL_STOCK_CODES = (
    "AMAZONFEE",
    "B",
    "POST",
    "DOT",
    "M",
    "BANK CHARGES",
)


def normalize_stock_codes(values: pd.Series) -> pd.Series:
    """Normalize stock codes exactly as the SQL ``UPPER(TRIM())`` rule."""
    return values.astype("string").str.strip().str.upper()


def build_analysis_datasets(
    frame: pd.DataFrame,
    special_codes: Iterable[str] = SPECIAL_STOCK_CODES,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build deduplicated candidate, product, and customer analysis data."""
    sales_df = frame.copy()
    sales_df["InvoiceDate"] = pd.to_datetime(
        sales_df["InvoiceDate"], errors="coerce"
    )
    sales_df["CustomerID"] = pd.to_numeric(
        sales_df["CustomerID"], errors="coerce"
    ).astype("Int64")
    sales_df = sales_df.drop_duplicates().copy()

    candidate_mask = (
        sales_df["Quantity"].gt(0)
        & sales_df["UnitPrice"].gt(0)
        & ~sales_df["InvoiceNo"].str.startswith("C", na=False)
    )
    sales_candidate_df = sales_df.loc[candidate_mask].copy()

    normalized_special_codes = {
        str(code).strip().upper() for code in special_codes
    }
    normalized_stock_codes = normalize_stock_codes(
        sales_candidate_df["StockCode"]
    )
    special_code_mask = normalized_stock_codes.isin(normalized_special_codes)

    product_sales_df = sales_candidate_df.loc[~special_code_mask].copy()
    product_sales_df["SalesAmount"] = (
        product_sales_df["Quantity"] * product_sales_df["UnitPrice"]
    )

    customer_columns = [
        "InvoiceNo",
        "StockCode",
        "Description",
        "Quantity",
        "InvoiceDate",
        "UnitPrice",
        "CustomerID",
        "Country",
        "SalesAmount",
    ]
    customer_sales_df = product_sales_df.loc[
        product_sales_df["CustomerID"].notna(), customer_columns
    ].copy()

    return (
        sales_df,
        sales_candidate_df,
        product_sales_df,
        customer_sales_df,
    )

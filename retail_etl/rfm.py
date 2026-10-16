"""Reusable RFM metric, scoring, and segmentation rules."""

import numpy as np
import pandas as pd


RFM_SEGMENT_ORDER = (
    "Champions",
    "Loyal Customers",
    "Potential Loyalists",
    "New Customers",
    "At Risk",
    "Hibernating",
)


def build_rfm_table(
    customer_sales_df: pd.DataFrame,
    *,
    data_max_date: pd.Timestamp | None = None,
) -> tuple[pd.Timestamp, pd.DataFrame]:
    """Return the reference date and customer-level RFM metrics."""
    maximum_date = pd.Timestamp(
        data_max_date
        if data_max_date is not None
        else customer_sales_df["InvoiceDate"].max()
    ).normalize()
    analysis_date = maximum_date + pd.DateOffset(days=1)

    rfm_df = customer_sales_df.groupby("CustomerID").agg(
        last_purchase_date=("InvoiceDate", "max"),
        frequency=("InvoiceNo", "nunique"),
        monetary=("SalesAmount", "sum"),
    )
    rfm_df["recency_days"] = (
        analysis_date - rfm_df["last_purchase_date"].dt.normalize()
    ).dt.days
    rfm_df = rfm_df[
        ["recency_days", "frequency", "monetary", "last_purchase_date"]
    ].sort_index()

    return analysis_date, rfm_df


def sql_ntile_score(
    frame: pd.DataFrame,
    column: str,
    *,
    ascending: bool = True,
    n_tiles: int = 5,
    tie_breaker: str = "CustomerID",
) -> pd.Series:
    """Match SQL NTILE with stable secondary ordering for any row count."""
    if not isinstance(n_tiles, int) or n_tiles <= 0:
        raise ValueError("n_tiles 必须是正整数。")
    if column not in frame.columns:
        raise KeyError(f"评分字段不存在：{column}")

    ordered = frame.reset_index()
    if tie_breaker not in ordered.columns:
        raise KeyError(f"并列值次级排序字段不存在：{tie_breaker}")
    ordered = ordered.sort_values(
        [column, tie_breaker],
        ascending=[ascending, True],
        kind="mergesort",
    )

    row_count = len(ordered)
    if row_count == 0:
        return pd.Series(index=frame.index, dtype="int8")

    base_size, larger_tile_count = divmod(row_count, n_tiles)
    tile_numbers = np.concatenate(
        [
            np.full(
                base_size + (tile_number <= larger_tile_count),
                tile_number,
                dtype="int8",
            )
            for tile_number in range(1, n_tiles + 1)
        ]
    )
    scores = pd.Series(tile_numbers, index=ordered[tie_breaker], dtype="int8")
    return scores.reindex(frame.index)


def assign_rfm_segments(rfm_scored_df: pd.DataFrame) -> pd.DataFrame:
    """Assign mutually exclusive business segments from R and F scores."""
    segmented_df = rfm_scored_df.copy()
    conditions = [
        (
            segmented_df["r_score"].ge(4)
            & segmented_df["f_score"].ge(4)
            & segmented_df["m_score"].ge(4)
        ),
        segmented_df["r_score"].ge(3) & segmented_df["f_score"].ge(4),
        segmented_df["r_score"].ge(4) & segmented_df["f_score"].le(2),
        segmented_df["r_score"].le(2) & segmented_df["f_score"].ge(3),
        segmented_df["r_score"].le(2) & segmented_df["f_score"].le(2),
    ]
    names = [
        "Champions",
        "Loyal Customers",
        "New Customers",
        "At Risk",
        "Hibernating",
    ]
    segmented_df["customer_segment"] = np.select(
        conditions,
        names,
        default="Potential Loyalists",
    )
    segmented_df["customer_segment"] = pd.Categorical(
        segmented_df["customer_segment"],
        categories=RFM_SEGMENT_ORDER,
        ordered=True,
    )
    return segmented_df

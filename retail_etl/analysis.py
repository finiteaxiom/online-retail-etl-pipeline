"""Pure calculations extracted from the original analysis Notebook.

Shares are fractions unless the field name ends in _pct. Product grouping
retains the original (StockCode, Description) grain.
"""

import numpy as np
import pandas as pd

from .cleaning import SPECIAL_STOCK_CODES, normalize_stock_codes
from .rfm import assign_rfm_segments, build_rfm_table, sql_ntile_score


def audit_data_quality(frame):
    checks = {
        "cancelled_invoice": frame.InvoiceNo.str.startswith("C", na=False),
        "negative_quantity": frame.Quantity.lt(0),
        "zero_quantity": frame.Quantity.eq(0),
        "negative_price": frame.UnitPrice.lt(0),
        "zero_price": frame.UnitPrice.eq(0),
        "special_stock_code": normalize_stock_codes(frame.StockCode).isin(SPECIAL_STOCK_CODES),
        "missing_description": frame.Description.isna(),
        "missing_customer": frame.CustomerID.isna(),
        "extra_duplicate": frame.duplicated(),
    }
    return pd.DataFrame([
        {"issue": key, "row_count": int(mask.sum()), "row_share": float(mask.mean())}
        for key, mask in checks.items()
    ])


def calculate_core_metrics(products, customers):
    total = float(products.SalesAmount.sum())
    orders = products.InvoiceNo.nunique()
    return pd.DataFrame([{
        "detail_rows": len(products), "orders": orders,
        "customers": customers.CustomerID.nunique(),
        "units": int(products.Quantity.sum()), "sales": total,
        "products": products.StockCode.nunique(),
        "customer_orders": customers.InvoiceNo.nunique(),
        "average_order_sales": total / orders,
        "identified_customer_sales_share": float(customers.SalesAmount.sum()) / total,
    }])


def aggregate_monthly_sales(products, excluded_months):
    result = products.assign(month=products.InvoiceDate.dt.to_period("M")).groupby("month").agg(
        sales=("SalesAmount", "sum"), orders=("InvoiceNo", "nunique"),
        active_days=("InvoiceDate", lambda s: s.dt.normalize().nunique()),
    ).sort_index()
    # Preserve all months; excluded months remain visible with no growth value.
    result["included_in_comparison"] = ~result.index.astype(str).isin(excluded_months)
    # Reindex before pct_change so a missing month never becomes a one-month comparison.
    calendar = pd.period_range(result.index.min(), result.index.max(), freq="M")
    sales = result.sales.reindex(calendar)
    sales.loc[sales.index.astype(str).isin(excluded_months)] = np.nan
    result["month_over_month_growth"] = sales.pct_change(fill_method=None).reindex(result.index)
    result.index = result.index.astype(str)
    return result.reset_index()


def aggregate_country_sales(products):
    result = products.groupby("Country").agg(
        sales=("SalesAmount", "sum"), orders=("InvoiceNo", "nunique"), units=("Quantity", "sum"),
    ).sort_values("sales", ascending=False)
    result["sales_share"] = result.sales / products.SalesAmount.sum()
    return result.reset_index()


def aggregate_product_sales(products):
    result = products.groupby(["StockCode", "Description"]).agg(
        sales=("SalesAmount", "sum"), units=("Quantity", "sum"), orders=("InvoiceNo", "nunique"),
    ).sort_values("sales", ascending=False)
    result["weighted_average_unit_price"] = result.sales / result.units
    return result.reset_index()


def build_customer_summary(customers):
    result = customers.groupby("CustomerID").agg(
        total_amount=("SalesAmount", "sum"), order_count=("InvoiceNo", "nunique"),
        last_purchase_date=("InvoiceDate", "max"),
    )
    result["average_order_amount"] = result.total_amount / result.order_count
    result["customer_type"] = np.where(result.order_count.ge(2), "Repeat customer", "One-time customer")
    return result


def summarize_repeat_customers(summary):
    result = summary.groupby("customer_type").agg(
        customers=("order_count", "size"), sales=("total_amount", "sum"),
        average_customer_value=("total_amount", "mean"),
        median_customer_value=("total_amount", "median"),
        average_order_count=("order_count", "mean"),
        average_order_amount=("average_order_amount", "mean"),
    )
    result["customer_share"] = result.customers / len(summary)
    result["sales_share"] = result.sales / summary.total_amount.sum()
    return result.reset_index()


def summarize_rfm_segments(rfm):
    result = rfm.groupby("customer_segment", observed=True, as_index=False).agg(
        customer_count=("monetary", "size"), total_sales_amount=("monetary", "sum"),
        average_customer_amount=("monetary", "mean"), median_customer_amount=("monetary", "median"),
        average_recency_days=("recency_days", "mean"), average_frequency=("frequency", "mean"),
    )
    result["customer_share_pct"] = result.customer_count / len(rfm) * 100
    result["sales_share_pct"] = result.total_sales_amount / rfm.monetary.sum() * 100
    return result.sort_values("total_sales_amount", ascending=False).reset_index(drop=True)


def build_analysis_tables(products, customers, excluded_months):
    if products.empty or customers.empty:
        raise ValueError("Full analysis requires product sales and identified customer sales")
    summary = build_customer_summary(customers)
    date, rfm = build_rfm_table(customers, data_max_date=products.InvoiceDate.max())
    for prefix, field, ascending in (
        ("r", "recency_days", False), ("f", "frequency", True), ("m", "monetary", True),
    ):
        rfm[f"{prefix}_score"] = sql_ntile_score(rfm, field, ascending=ascending)
    rfm = assign_rfm_segments(rfm)
    rfm["analysis_date"] = date
    can_correlate = len(summary) >= 2 and summary.order_count.nunique() > 1 and summary.total_amount.nunique() > 1
    profile = pd.DataFrame([{
        "customer_count": len(summary), "repeat_customer_count": int(summary.order_count.ge(2).sum()),
        "repeat_customer_rate": float(summary.order_count.ge(2).mean()),
        "mean_customer_spend": summary.total_amount.mean(),
        "median_customer_spend": summary.total_amount.median(),
        "mean_order_frequency": summary.order_count.mean(),
        "median_order_frequency": summary.order_count.median(),
        "mean_customer_order_amount": summary.average_order_amount.mean(),
        "median_customer_order_amount": summary.average_order_amount.median(),
        "frequency_spend_pearson": summary.order_count.corr(summary.total_amount) if can_correlate else np.nan,
        "frequency_spend_rank_correlation": summary.order_count.rank().corr(summary.total_amount.rank()) if can_correlate else np.nan,
    }])
    concentration = summary.copy()
    concentration["customer_decile"] = sql_ntile_score(concentration, "total_amount", ascending=False, n_tiles=10)
    deciles = concentration.groupby("customer_decile").agg(
        customer_count=("total_amount", "size"), sales=("total_amount", "sum"),
    )
    deciles["sales_share"] = deciles.sales / summary.total_amount.sum()
    return {
        "core_metrics": calculate_core_metrics(products, customers),
        "monthly_sales": aggregate_monthly_sales(products, excluded_months),
        "country_sales": aggregate_country_sales(products),
        "product_sales_summary": aggregate_product_sales(products),
        "customer_summary": summary.reset_index(),
        "customer_metrics": profile,
        "repeat_customers": summarize_repeat_customers(summary),
        "customer_concentration": deciles.reset_index(),
        "customer_rfm": rfm.reset_index(),
        "rfm_segments": summarize_rfm_segments(rfm),
    }

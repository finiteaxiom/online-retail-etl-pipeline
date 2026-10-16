import unittest

import pandas as pd

from retail_etl.analysis import aggregate_monthly_sales, build_analysis_tables
from retail_etl.cleaning import build_analysis_datasets
from retail_etl.extract import validate_and_convert, validate_required_columns
from retail_etl.load import make_engine
from retail_etl.rfm import sql_ntile_score


def sample():
    return pd.DataFrame([
        ["1", "A", "Item", "2", "12/1/2010 8:26", "2.55", "1", "UK"],
        ["1", "A", "Item", "2", "12/1/2010 8:26", "2.55", "1", "UK"],
        ["2", " m ", "Manual", "1", "12/2/2010 8:26", "9", "1", "UK"],
        ["C3", "A", "Item", "1", "12/3/2010 8:26", "2", "1", "UK"],
        ["4", "A", "Item", "1", "12/4/2010 8:26", "3", None, "UK"],
        ["5", "A", "Item", "-1", "12/5/2010 8:26", "2", "1", "UK"],
    ], columns=["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID", "Country"]).astype("string")


class PipelineTests(unittest.TestCase):
    def test_cleaning_preserves_anonymous_sales_and_removes_special_codes(self):
        raw = sample()
        converted = validate_and_convert(raw)
        clean, candidates, products, customers = build_analysis_datasets(converted)
        self.assertEqual([len(f) for f in (clean, candidates, products, customers)], [5, 3, 2, 1])
        self.assertAlmostEqual(products.SalesAmount.sum(), 8.1)
        self.assertAlmostEqual(customers.SalesAmount.sum(), 5.1)
        self.assertEqual(len(raw), 6)

    def test_invalid_date_stops_batch(self):
        raw = sample()
        raw.loc[0, "InvoiceDate"] = "bad date"
        with self.assertRaisesRegex(ValueError, "Invalid InvoiceDate"):
            validate_and_convert(raw)

    def test_fractional_customer_id_and_invalid_quantity_stop_batch(self):
        for column, bad in (("CustomerID", "1.5"), ("Quantity", "two"), ("UnitPrice", "inf")):
            with self.subTest(column=column):
                raw = sample()
                raw.loc[0, column] = bad
                with self.assertRaises(ValueError):
                    validate_and_convert(raw)

    def test_missing_schema_rejected(self):
        with self.assertRaises(ValueError):
            validate_required_columns(sample().drop(columns="InvoiceNo"))

    def test_missing_or_excluded_month_does_not_bridge_growth(self):
        frame = pd.DataFrame({
            "InvoiceDate": pd.to_datetime(["2011-01-01", "2011-03-01", "2011-04-01"]),
            "SalesAmount": [10., 20., 30.], "InvoiceNo": ["1", "2", "3"],
        })
        result = aggregate_monthly_sales(frame, [])
        self.assertTrue(pd.isna(result.loc[1, "month_over_month_growth"]))
        self.assertAlmostEqual(result.loc[2, "month_over_month_growth"], .5)
        result = aggregate_monthly_sales(frame, ["2011-03"])
        self.assertFalse(result.loc[1, "included_in_comparison"])
        self.assertTrue(pd.isna(result.loc[2, "month_over_month_growth"]))

    def test_ntile_ties_are_stable_and_small_population_is_supported(self):
        frame = pd.DataFrame({"frequency": [1, 1, 1]}, index=pd.Index([3, 1, 2], name="CustomerID"))
        self.assertEqual(sql_ntile_score(frame, "frequency").tolist(), [3, 1, 2])

    def test_full_analysis_reference_date_uses_all_product_sales(self):
        _, _, products, customers = build_analysis_datasets(validate_and_convert(sample()))
        tables = build_analysis_tables(products, customers, [])
        self.assertEqual(tables["customer_rfm"].analysis_date.iloc[0], pd.Timestamp("2010-12-05"))
        self.assertAlmostEqual(tables["core_metrics"].sales.iloc[0], 8.1)

    def test_database_guard_rejects_old_database(self):
        with self.assertRaises(ValueError):
            make_engine("localhost", 3306, "root", "unused", "online_retail_analysis")


if __name__ == "__main__":
    unittest.main()

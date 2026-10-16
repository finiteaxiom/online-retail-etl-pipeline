-- Run in SQLyog after a successful full ETL batch.
USE online_retail_etl;

SELECT run_id, COUNT(*) AS detail_rows,
       COUNT(DISTINCT InvoiceNo) AS orders,
       COUNT(DISTINCT CustomerID) AS customers,
       SUM(Quantity) AS units, ROUND(SUM(SalesAmount), 2) AS sales
FROM product_sales GROUP BY run_id;

SELECT * FROM core_metrics;
SELECT * FROM pipeline_counts;
SELECT * FROM quality_audit;
SELECT * FROM monthly_sales ORDER BY month;
SELECT * FROM country_sales ORDER BY sales DESC;
SELECT * FROM product_sales_summary ORDER BY sales DESC LIMIT 10;
SELECT * FROM repeat_customers;
SELECT * FROM customer_metrics;
SELECT * FROM customer_concentration ORDER BY customer_decile;
SELECT * FROM rfm_segments ORDER BY total_sales_amount DESC;

-- These sums should agree (identified customers only).
SELECT 'customer_sales' AS source, ROUND(SUM(SalesAmount), 2) AS sales FROM customer_sales
UNION ALL SELECT 'customer_summary', ROUND(SUM(total_amount), 2) FROM customer_summary
UNION ALL SELECT 'customer_rfm', ROUND(SUM(monetary), 2) FROM customer_rfm
UNION ALL SELECT 'rfm_segments', ROUND(SUM(total_sales_amount), 2) FROM rfm_segments;

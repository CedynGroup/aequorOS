SELECT bi_dim_date.calendar_month AS k0, max(CASE WHEN (bi_fact_engine_metric.module = ? AND bi_fact_engine_metric.metric_id = ? AND bi_fact_engine_metric.tier = ? AND bi_fact_engine_metric.regime = ?) THEN bi_fact_engine_metric.value END) AS m0
FROM bi_fact_engine_metric LEFT OUTER JOIN bi_dim_date ON bi_dim_date.organization_id = bi_fact_engine_metric.organization_id AND bi_dim_date.bank_id = bi_fact_engine_metric.bank_id AND bi_dim_date.date = bi_fact_engine_metric.as_of_date
WHERE bi_fact_engine_metric.organization_id = ? AND bi_fact_engine_metric.bank_id = ? AND bi_fact_engine_metric.as_of_date IN (SELECT max(bi_dim_date.date) AS max_1
FROM bi_dim_date
WHERE bi_dim_date.organization_id = ? AND bi_dim_date.bank_id = ? AND bi_dim_date.has_data IS 1 AND bi_dim_date.date BETWEEN ? AND ? GROUP BY bi_dim_date.calendar_month) GROUP BY bi_dim_date.calendar_month ORDER BY k0 ASC NULLS LAST
 LIMIT ? OFFSET ?

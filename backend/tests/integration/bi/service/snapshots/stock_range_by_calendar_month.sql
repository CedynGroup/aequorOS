SELECT bi_dim_date.calendar_month AS k0, sum(CASE WHEN (bi_agg_position_daily.position_type IN (?)) THEN bi_agg_position_daily.balance_rc_sum END) AS m0
FROM bi_agg_position_daily LEFT OUTER JOIN bi_dim_date ON bi_dim_date.organization_id = bi_agg_position_daily.organization_id AND bi_dim_date.bank_id = bi_agg_position_daily.bank_id AND bi_dim_date.date = bi_agg_position_daily.as_of_date
WHERE bi_agg_position_daily.organization_id = ? AND bi_agg_position_daily.bank_id = ? AND bi_agg_position_daily.as_of_date BETWEEN ? AND ? AND bi_agg_position_daily.as_of_date IN (SELECT max(bi_dim_date.date) AS max_1
FROM bi_dim_date
WHERE bi_dim_date.organization_id = ? AND bi_dim_date.bank_id = ? AND bi_dim_date.has_data IS 1 AND bi_dim_date.date BETWEEN ? AND ? GROUP BY bi_dim_date.calendar_month) GROUP BY bi_dim_date.calendar_month ORDER BY k0 ASC NULLS LAST
 LIMIT ? OFFSET ?

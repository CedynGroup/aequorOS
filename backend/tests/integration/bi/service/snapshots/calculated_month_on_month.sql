SELECT sum(CASE WHEN (bi_agg_position_daily.position_type IN (?) AND bi_agg_position_daily.as_of_date = ?) THEN bi_agg_position_daily.balance_rc_sum END) AS m0, CAST(sum(CASE WHEN (bi_agg_position_daily.position_type IN (?) AND bi_agg_position_daily.as_of_date BETWEEN ? AND ? AND bi_agg_position_daily.as_of_date = (SELECT max(bi_dim_date.date) AS max_1
FROM bi_dim_date
WHERE bi_dim_date.organization_id = ? AND bi_dim_date.bank_id = ? AND bi_dim_date.has_data IS 1 AND bi_dim_date.date BETWEEN ? AND ?)) THEN bi_agg_position_daily.balance_rc_sum END) - sum(CASE WHEN (bi_agg_position_daily.position_type IN (?) AND bi_agg_position_daily.as_of_date BETWEEN ? AND ? AND bi_agg_position_daily.as_of_date = (SELECT max(bi_dim_date.date) AS max_2
FROM bi_dim_date
WHERE bi_dim_date.organization_id = ? AND bi_dim_date.bank_id = ? AND bi_dim_date.has_data IS 1 AND bi_dim_date.date BETWEEN ? AND ?)) THEN bi_agg_position_daily.balance_rc_sum END) AS FLOAT) / (nullif(sum(CASE WHEN (bi_agg_position_daily.position_type IN (?) AND bi_agg_position_daily.as_of_date BETWEEN ? AND ? AND bi_agg_position_daily.as_of_date = (SELECT max(bi_dim_date.date) AS max_2
FROM bi_dim_date
WHERE bi_dim_date.organization_id = ? AND bi_dim_date.bank_id = ? AND bi_dim_date.has_data IS 1 AND bi_dim_date.date BETWEEN ? AND ?)) THEN bi_agg_position_daily.balance_rc_sum END), ?) + 0.0) AS m1
FROM bi_agg_position_daily
WHERE bi_agg_position_daily.organization_id = ? AND bi_agg_position_daily.bank_id = ? AND (bi_agg_position_daily.as_of_date = ? OR bi_agg_position_daily.as_of_date BETWEEN ? AND ? AND bi_agg_position_daily.as_of_date = (SELECT max(bi_dim_date.date) AS max_1
FROM bi_dim_date
WHERE bi_dim_date.organization_id = ? AND bi_dim_date.bank_id = ? AND bi_dim_date.has_data IS 1 AND bi_dim_date.date BETWEEN ? AND ?) OR bi_agg_position_daily.as_of_date BETWEEN ? AND ? AND bi_agg_position_daily.as_of_date = (SELECT max(bi_dim_date.date) AS max_2
FROM bi_dim_date
WHERE bi_dim_date.organization_id = ? AND bi_dim_date.bank_id = ? AND bi_dim_date.has_data IS 1 AND bi_dim_date.date BETWEEN ? AND ?))
 LIMIT ? OFFSET ?

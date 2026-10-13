SELECT bi_dim_branch.region AS k0, CAST(sum(CASE WHEN (bi_agg_position_daily.position_type IN (?)) THEN bi_agg_position_daily.balance_rc_sum END) AS FLOAT) / (nullif(sum(CASE WHEN (bi_agg_position_daily.position_type IN (?)) THEN bi_agg_position_daily.balance_rc_sum END), ?) + 0.0) AS m0
FROM bi_agg_position_daily LEFT OUTER JOIN bi_dim_branch ON bi_dim_branch.organization_id = bi_agg_position_daily.organization_id AND bi_dim_branch.bank_id = bi_agg_position_daily.bank_id AND bi_dim_branch.branch_code = bi_agg_position_daily.branch_code
WHERE bi_agg_position_daily.organization_id = ? AND bi_agg_position_daily.bank_id = ? AND bi_agg_position_daily.as_of_date = ? GROUP BY bi_dim_branch.region ORDER BY k0 ASC NULLS LAST
 LIMIT ? OFFSET ?

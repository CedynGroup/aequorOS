SELECT l1.k0 AS k0, CAST(sum(l1.m0s * l1.m0s) AS FLOAT) / (nullif(sum(l1.m0s) * sum(l1.m0s), ?) + 0.0) AS m0, sum(l1.m1v) AS m1
FROM (SELECT bi_dim_branch.branch_code AS k0, bi_fact_position_daily.sector AS over, sum(CASE WHEN (bi_fact_position_daily.position_type IN (?)) THEN bi_fact_position_daily.classification_exposure_rc END) AS m0s, sum(CASE WHEN (bi_fact_position_daily.position_type IN (?)) THEN bi_fact_position_daily.balance_rc END) AS m1v
FROM bi_fact_position_daily LEFT OUTER JOIN bi_dim_branch ON bi_dim_branch.organization_id = bi_fact_position_daily.organization_id AND bi_dim_branch.bank_id = bi_fact_position_daily.bank_id AND bi_dim_branch.branch_code = bi_fact_position_daily.branch_code
WHERE bi_fact_position_daily.organization_id = ? AND bi_fact_position_daily.bank_id = ? AND bi_fact_position_daily.as_of_date = ? GROUP BY bi_dim_branch.branch_code, bi_fact_position_daily.sector) AS l1 GROUP BY l1.k0 ORDER BY k0 ASC NULLS LAST
 LIMIT ? OFFSET ?

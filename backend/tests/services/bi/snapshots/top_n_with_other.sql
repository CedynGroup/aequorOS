SELECT CASE WHEN (bi_fact_position_daily.sector IN (SELECT topn.k0
FROM (SELECT bi_fact_position_daily.sector AS k0, sum(CASE WHEN (bi_fact_position_daily.position_type IN (?)) THEN bi_fact_position_daily.balance_rc END) AS m0
FROM bi_fact_position_daily
WHERE bi_fact_position_daily.organization_id = ? AND bi_fact_position_daily.bank_id = ? AND bi_fact_position_daily.as_of_date = ? GROUP BY bi_fact_position_daily.sector ORDER BY m0 DESC NULLS LAST
 LIMIT ? OFFSET ?) AS topn)) THEN CAST(bi_fact_position_daily.sector AS VARCHAR) WHEN (bi_fact_position_daily.sector IS NULL) THEN NULL ELSE ? END AS k0, sum(CASE WHEN (bi_fact_position_daily.position_type IN (?)) THEN bi_fact_position_daily.balance_rc END) AS m0
FROM bi_fact_position_daily
WHERE bi_fact_position_daily.organization_id = ? AND bi_fact_position_daily.bank_id = ? AND bi_fact_position_daily.as_of_date = ? GROUP BY CASE WHEN (bi_fact_position_daily.sector IN (SELECT topn.k0
FROM (SELECT bi_fact_position_daily.sector AS k0, sum(CASE WHEN (bi_fact_position_daily.position_type IN (?)) THEN bi_fact_position_daily.balance_rc END) AS m0
FROM bi_fact_position_daily
WHERE bi_fact_position_daily.organization_id = ? AND bi_fact_position_daily.bank_id = ? AND bi_fact_position_daily.as_of_date = ? GROUP BY bi_fact_position_daily.sector ORDER BY m0 DESC NULLS LAST
 LIMIT ? OFFSET ?) AS topn)) THEN CAST(bi_fact_position_daily.sector AS VARCHAR) WHEN (bi_fact_position_daily.sector IS NULL) THEN NULL ELSE ? END ORDER BY k0 ASC NULLS LAST
 LIMIT ? OFFSET ?

SELECT bi_fact_loan_event.event_type AS k0, sum(bi_fact_loan_event.amount_rc) AS m0, count(bi_fact_loan_event.event_id) AS m1
FROM bi_fact_loan_event
WHERE bi_fact_loan_event.organization_id = ? AND bi_fact_loan_event.bank_id = ? AND bi_fact_loan_event.event_date BETWEEN ? AND ? GROUP BY bi_fact_loan_event.event_type ORDER BY k0 ASC NULLS LAST
 LIMIT ? OFFSET ?

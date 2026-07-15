CREATE OR REPLACE VIEW calc.vw_balance_sheet_reconciliation AS
SELECT
    bank_id,
    as_of_date,
    SUM(CASE WHEN category_group = 'asset' THEN amount_ghs_m ELSE 0 END) AS total_assets_ghs_m,
    SUM(CASE WHEN category_group = 'liability' THEN amount_ghs_m ELSE 0 END) AS total_liabilities_ghs_m,
    SUM(CASE WHEN category_group = 'capital' THEN amount_ghs_m ELSE 0 END) AS total_capital_ghs_m,
    SUM(CASE WHEN category_group = 'asset' THEN amount_ghs_m ELSE 0 END)
      - SUM(CASE WHEN category_group IN ('liability', 'capital') THEN amount_ghs_m ELSE 0 END)
      AS balance_gap_ghs_m
FROM risk.fact_balance_sheet_position
GROUP BY bank_id, as_of_date;

CREATE OR REPLACE VIEW calc.vw_loan_book_reconciliation AS
SELECT
    l.bank_id,
    l.as_of_date,
    SUM(l.ead_ghs_m) AS loan_exposure_total_ghs_m,
    b.amount_ghs_m AS balance_sheet_loans_ghs_m,
    SUM(l.ead_ghs_m) - b.amount_ghs_m AS loan_gap_ghs_m
FROM risk.fact_loan_exposure l
JOIN risk.fact_balance_sheet_position b
  ON b.bank_id = l.bank_id
 AND b.as_of_date = l.as_of_date
 AND b.category_code = 'gross_loans'
GROUP BY l.bank_id, l.as_of_date, b.amount_ghs_m;

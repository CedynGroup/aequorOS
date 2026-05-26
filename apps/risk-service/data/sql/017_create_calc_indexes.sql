CREATE INDEX IF NOT EXISTS ix_balance_sheet_bank_date
    ON risk.fact_balance_sheet_position (bank_id, as_of_date);

CREATE INDEX IF NOT EXISTS ix_loan_exposure_bank_date
    ON risk.fact_loan_exposure (bank_id, as_of_date);

CREATE INDEX IF NOT EXISTS ix_securities_bank_date
    ON risk.fact_securities_holding (bank_id, as_of_date);

CREATE INDEX IF NOT EXISTS ix_deposit_behavior_bank_date
    ON risk.fact_deposit_behavior (bank_id, as_of_date);

CREATE INDEX IF NOT EXISTS ix_nsfr_asf_bank_date
    ON risk.fact_nsfr_asf_base (bank_id, as_of_date);

CREATE INDEX IF NOT EXISTS ix_nsfr_rsf_bank_date
    ON risk.fact_nsfr_rsf_base (bank_id, as_of_date);

CREATE INDEX IF NOT EXISTS ix_run_bank_date_module
    ON calc.calc_run (bank_id, as_of_date, module_code);

CREATE INDEX IF NOT EXISTS ix_metric_result_run
    ON calc.calc_metric_result (run_id, metric_code);

CREATE INDEX IF NOT EXISTS ix_line_item_run
    ON calc.calc_line_item (run_id, metric_code);

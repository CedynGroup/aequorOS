# BSD2 — Statement of Assets and Liabilities: line / cell map

**Official workbook:** `FORM BSD2 REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days · **Basis:** solo · **Unit:** ¢'Million (all sheets; Annex 17 = counts)

**Sheets (22, official order):** `BSD2`, `BSD2-Summary`, `BSD2-Annex 1`, `BSD2-Annex 2a`, `BSD2-Annex 2b`, `BSD2-Annex 2c`, `BSD2-Annex 2d`, `BSD2-Annex 3`, `BSD2-Annex 4`, `BSD2-Annex 5`, `BSD2-Annex 6`, `BSD2-Annex7`, `BSD2-Annex 8`, `BSD2-Annex 9`, `BSD2-Annex 10`, `BSD2-Annex 11`, `BSD2-Annex 12`, `BSD2-Annex 13`, `BSD2-Annex 14`, `BSD2-Annex 15`, `BSD2-Annex 16`, `BSD2-Annex 17`

Generated from `bog_forms/linemaps/bsd2.py` + `layouts/BSD2.json` (do not hand-edit; regenerate).

## Sheet `BSD2` — 410 input cells (205 leaf rows × Domestic B / Foreign C) · 415 template formulas (TOTAL column D, ▲ subtotals, section totals)

Status legend — **mapped**: fed from platform data via the named resolver; **input_required**: bank must supply (no canonical source yet); **coa-mapping**: bank-specific chart-of-accounts split required. Domestic/Foreign follow the Guide (payable in cedis vs foreign currency).

| Row | Official line | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|
| 7 | 1.   Foreign currency notes and coins | mapped | `positions.sum` position_types=['CASH']; attribute_eq={'instrument': 'fx_notes_coins'} |  |
| 8 | 2.   Correspondent acc. in non-res. financial inst. | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT', 'CASH']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=False |  |
| 9 | 3.   Other claims on non-residents | mapped | `positions.sum` position_types=['OTHER_ASSET', 'SECURITY_HOLDING']; resident=False |  |
| 10 | 4. Loans and advances to non-resident | mapped | `positions.sum` position_types=['LOAN']; resident=False |  |
| 11 | 5. Equity and Other non-liquid investments abroad | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; resident=False; regulatory_categories=['EQUITY', 'EQUITY_INVESTMENT'] | equity/non-liquid investments abroad by product category |
| 14 | (a) Cash on hand | mapped | `facts.sum` group=balance_sheet; categories=['cash_vault'] |  |
| 16 | (i) Sight balances  (Annex 2a) | mapped | `facts.sum` group=balance_sheet; categories=['bog_excess_reserves'] |  |
| 17 | (ii) Special deposits (Annex 2b) | mapped | `facts.sum` group=balance_sheet; categories=['bog_required_reserves'] |  |
| 18 | (iii) Swaps deal receivable (Annex 2c) | mapped | `positions.sum` position_types=['FX_HEDGE', 'DERIVATIVE']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'leg': 'receivable'} |  |
| 19 | (iv) Repos receivable  (Annex 2d) | mapped | `positions.sum` position_types=['OTHER_ASSET']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'repo_receivable'} |  |
| 20 | (v) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '20'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=20 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 22 | (i) Commercial banks | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=True |  |
| 23 | (ii) Rural banks | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'rural_bank'} |  |
| 25 | Money at call | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'discount_house', 'tenor': 'call'} |  |
| 26 | Other balances | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'discount_house'} |  |
| 27 | (iv) Savings and loans associations | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'savings_and_loans'} |  |
| 28 | (v) Credit unions | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'credit_union'} |  |
| 29 | (vi) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '29'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=29 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 31 | (i)Balances | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT', 'OTHER_ASSET']; counterparty_types=['NBFI']; resident=True |  |
| 32 | (ii) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '32'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=32 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 33 | (e) Cheques for clearing drawn on banks | mapped | `positions.sum` position_types=['OTHER_ASSET']; attribute_eq={'instrument': 'cheques_for_clearing'} |  |
| 36 | (i) 91 Day | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'tbill', 'tenor_days': 91} |  |
| 37 | (ii) 182 Day | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'tbill', 'tenor_days': 182} |  |
| 38 | (iii) 1Year Bond/Stock | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'gog_bond', 'tenor_years': 1} |  |
| 39 | (iv) Other Bills | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'tbill_other'} |  |
| 41 | (i) 28 Day bill | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_bill', 'tenor_days': 28} |  |
| 42 | (ii) 56 Day bill | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_bill', 'tenor_days': 56} |  |
| 43 | (iii) 91 Day bill | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_bill', 'tenor_days': 91} |  |
| 44 | (iv) 182 Day bill | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_bill', 'tenor_days': 182} |  |
| 45 | (v) 1Year Bond/Stock | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_bond', 'tenor_years': 1} |  |
| 46 | (vi) Others | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_other'} |  |
| 48 | (i)  Commercial Bank | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=True |  |
| 49 | (ii)  Discount House | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'institution_class': 'discount_house'} |  |
| 50 | (iii) Other Depository | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'institution_class': 'other_depository'} |  |
| 51 | (d) Other financial institutions | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'institution_class': 'other_financial'} |  |
| 52 | (e) Public institutions | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'issuer_class': 'public_institution'} |  |
| 54 | (i) Cocoa bills | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; attribute_eq={'instrument': 'cocoa_bill'} |  |
| 55 | (ii) Grains bills | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; attribute_eq={'instrument': 'grains_bill'} |  |
| 56 | (iii) Other bills | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'issuer_class': 'public_enterprise', 'instrument': 'bill'} |  |
| 58 | (i) Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CORPORATE', 'SME']; regulatory_categories=['BOND', 'CORPORATE_BOND'] |  |
| 59 | (ii) Stocks | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CORPORATE', 'SME']; regulatory_categories=['EQUITY', 'EQUITY_INVESTMENT'] |  |
| 61 | (a)  Government | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['SOVEREIGN'] |  |
| 62 | (b)  Public institutions | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'borrower_class': 'public_institution'} |  |
| 64 | (i) Cocoa Syndicated Loan | mapped | `positions.sum` position_types=['LOAN']; attribute_eq={'scheme': 'cocoa_syndicated'} |  |
| 65 | (ii) Others | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'borrower_class': 'public_enterprise'} |  |
| 66 | (d)  Private enterprises | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['CORPORATE', 'SME'] |  |
| 67 | (e)  Individuals | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['RETAIL_INDIVIDUAL'] |  |
| 69 | Less: Total debt provision | mapped | `facts.sum` group=loan_exposure; categories=['specific_provision', 'total_debt_provision']; sign=-1 | total debt provision (contra) — from provisions where derived |
| 70 | Interest in suspense | input_required |  | interest in suspense — suspense sub-ledger required |
| 71 | Revaluation gains on non-performing loans | input_required |  | revaluation gains on NPLs — bank must supply |
| 74 | i)  GGILB | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'ggilb'} |  |
| 75 | ii)  TOR bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; attribute_eq={'instrument': 'tor_bond'} |  |
| 76 | (iv) 2 year Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'gog_bond', 'tenor_years': 2} |  |
| 77 | (v) 3 year Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'gog_bond', 'tenor_years': 3} |  |
| 78 | iii)  Others | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'gog_bond_other'} |  |
| 80 | i)  2-Year bond | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_bond', 'tenor_years': 2} |  |
| 81 | ii)  Others | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CENTRAL_BANK']; attribute_eq={'instrument': 'bog_bond_other'} |  |
| 84 | Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=True; regulatory_categories=['BOND'] |  |
| 85 | Other | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=True; regulatory_categories=['OTHER'] |  |
| 86 | ii)  Rural banks | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'institution_class': 'rural_bank'} |  |
| 87 | iii)  Discount houses | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'institution_class': 'discount_house', 'long_term': True} |  |
| 88 | iv)  Savings and loans companies | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'institution_class': 'savings_and_loans'} |  |
| 89 | v)  Credit unions | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'institution_class': 'credit_union'} |  |
| 91 | i)  Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; regulatory_categories=['BOND'] |  |
| 92 | ii)  Other | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; regulatory_categories=['OTHER'] |  |
| 94 | i)  Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['GOVERNMENT_ENTITY']; regulatory_categories=['BOND']; attribute_eq={'issuer_class': 'public_institution'} |  |
| 95 | ii)  Other | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['GOVERNMENT_ENTITY']; regulatory_categories=['OTHER']; attribute_eq={'issuer_class': 'public_institution'} |  |
| 97 | i)  Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['GOVERNMENT_ENTITY']; regulatory_categories=['BOND']; attribute_eq={'issuer_class': 'public_enterprise'} |  |
| 98 | ii)  Other | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['GOVERNMENT_ENTITY']; regulatory_categories=['OTHER']; attribute_eq={'issuer_class': 'public_enterprise'} |  |
| 100 | i)  Bonds | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CORPORATE', 'SME']; regulatory_categories=['BOND', 'CORPORATE_BOND']; attribute_eq={'long_term': True} |  |
| 101 | ii)  Other | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CORPORATE', 'SME']; regulatory_categories=['OTHER']; attribute_eq={'long_term': True} |  |
| 104 | i)  Commercial banks | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; attribute_eq={'relationship': 'subsidiary_or_associate'} |  |
| 105 | ii)  Rural banks | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'relationship': 'subsidiary_or_associate', 'institution_class': 'rural_bank'} |  |
| 106 | iii) Savings and loans companies | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'relationship': 'subsidiary_or_associate', 'institution_class': 'savings_and_loans'} |  |
| 107 | iv) Credit unions | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'relationship': 'subsidiary_or_associate', 'institution_class': 'credit_union'} |  |
| 108 | (b)  Other financial institutions | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['NBFI']; attribute_eq={'relationship': 'subsidiary_or_associate'} |  |
| 109 | (c)  Public enterprises | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'relationship': 'subsidiary_or_associate'} |  |
| 110 | (d)  Private enterprises | mapped | `positions.sum` position_types=['SECURITY_HOLDING']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'relationship': 'subsidiary_or_associate'} |  |
| 112 | Less: Impairment in value | input_required |  | impairment in value of investments — bank must supply |
| 113 | 11.  Other Assets  (Annex 6) | mapped | `facts.sum` group=balance_sheet; categories=['other_assets'] |  |
| 115 | (a)  Bank land and premises | mapped | `refs.sum` kind=capital_expenditure; value_field=closing_cost_ghs; asset_class=['land_buildings']; currency_field=currency | bank land and premises at cost — Σ capital_expenditure register closing_cost_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 116 | (b)  Land and Premises for staff and staff amenities | mapped | `refs.sum` kind=capital_expenditure; value_field=closing_cost_ghs; asset_class=['staff_land_premises']; currency_field=currency | land and premises for staff and staff amenities at cost — Σ capital_expenditure register closing_cost_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 117 | (c)  Computers | mapped | `refs.sum` kind=capital_expenditure; value_field=closing_cost_ghs; asset_class=['computers']; currency_field=currency | computers at cost — Σ capital_expenditure register closing_cost_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 118 | (d)  Furniture, fixtures and equipment | mapped | `refs.sum` kind=capital_expenditure; value_field=closing_cost_ghs; asset_class=['furniture_equipment', 'other_office_equipment']; currency_field=currency | furniture, fixtures and equipment at cost (BSD10's furniture and equipment + other office equipment classes) — Σ capital_expenditure register closing_cost_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 119 | (e)  Motor vehicles | mapped | `refs.sum` kind=capital_expenditure; value_field=closing_cost_ghs; asset_class=['motor_vehicles']; currency_field=currency | motor vehicles at cost — Σ capital_expenditure register closing_cost_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 120 | (f)  Other property acquired by legal rights | mapped | `refs.sum` kind=capital_expenditure; value_field=closing_cost_ghs; asset_class=['other_property_legal_rights']; currency_field=currency | other property acquired by legal rights at cost — Σ capital_expenditure register closing_cost_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 121 | (g)  Work - in - Progress | mapped | `refs.sum` kind=capital_expenditure; value_field=wip_closing_ghs; all classes; currency_field=currency | capital work-in-progress balance — Σ capital_expenditure register wip_closing_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 123 | Less Depreciation | mapped | `refs.sum` kind=capital_expenditure; value_field=accumulated_depreciation_ghs; all classes; currency_field=currency | accumulated depreciation — Σ capital_expenditure register accumulated_depreciation_ghs (fixed-asset sub-ledger stock at the latest period end on/before the reporting date; Domestic/Foreign by row currency) |
| 128 | 14.  Paid-up capital | mapped | `facts.sum` group=capital_component; categories=['ordinary_share_capital', 'paid_up_capital']; currency=all |  |
| 130 | (a)  Statutory reserves fund | mapped | `facts.sum` group=capital_component; categories=['statutory_reserve']; currency=all |  |
| 131 | (b)  Revaluation reserves (Capital Surplus) | mapped | `facts.sum` group=capital_component; categories=['revaluation_reserve']; currency=all |  |
| 132 | (c)   Income Surplus | mapped | `facts.sum` group=capital_component; categories=['income_surplus', 'retained_earnings']; currency=all |  |
| 133 | (d)  Profit and loss accounts to date | mapped | `facts.sum` group=capital_component; categories=['current_year_profit']; currency=all |  |
| 134 | (e)   Other reserves (Annex 7) | mapped | `facts.sum` group=capital_component; categories=['other_reserves']; currency=all |  |
| 136 | 17.  Other amounts allowed as capital | input_required |  | other amounts allowed as capital — bank must supply |
| 139 | (a)  Non-resident financial institutions | mapped | `positions.sum` position_types=['DEPOSIT', 'INTERBANK_BORROWING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=False |  |
| 140 | (b)  Other non-resident | mapped | `positions.sum` position_types=['DEPOSIT', 'INTERBANK_BORROWING', 'OTHER_LIABILITY']; resident=False |  |
| 141 | (c)  Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '141'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=141 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 143 | (a) Non-resident financial institutions | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=False; attribute_eq={'instrument': 'term_borrowing'} |  |
| 144 | (b) Other non-resident | mapped | `positions.sum` position_types=['INTERBANK_BORROWING', 'OTHER_LIABILITY']; resident=False; attribute_eq={'instrument': 'term_borrowing'} |  |
| 145 | (c)  Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '145'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=145 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 148 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'CURRENT'} |  |
| 149 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'CURRENT'} |  |
| 150 | (iii) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'CURRENT'} |  |
| 151 | (iv) Acrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '151'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=151 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 153 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'SAVINGS'} |  |
| 154 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'SAVINGS'} |  |
| 155 | (iii) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'SAVINGS'} |  |
| 156 | (iv) Acrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '156'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=156 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 158 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'FIXED'} |  |
| 159 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'FIXED'} |  |
| 160 | (iii) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'FIXED'} |  |
| 161 | (iv) Acrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '161'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=161 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 163 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'CALL'} |  |
| 164 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'CALL'} |  |
| 165 | (iii) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'CALL'} |  |
| 166 | (iv) Acrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '166'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=166 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 169 | (a) Bank of Ghana | mapped | `positions.sum` position_types=['INTERBANK_BORROWING', 'DEPOSIT']; counterparty_types=['CENTRAL_BANK'] |  |
| 171 | (i) Commercial banks | mapped | `positions.sum` position_types=['INTERBANK_BORROWING', 'DEPOSIT']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=True |  |
| 172 | (ii) Discount house | mapped | `positions.sum` position_types=['INTERBANK_BORROWING', 'DEPOSIT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'discount_house'} |  |
| 173 | (iii) Others | mapped | `positions.sum` position_types=['INTERBANK_BORROWING', 'DEPOSIT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'other_depository'} |  |
| 174 | (c) Other financial institutions | mapped | `positions.sum` position_types=['INTERBANK_BORROWING', 'DEPOSIT']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'other_financial'} |  |
| 175 | (d) Government | mapped | `positions.sum` position_types=['DEPOSIT', 'INTERBANK_BORROWING']; counterparty_types=['SOVEREIGN'] |  |
| 176 | (e) Others | input_required |  | other balances due — bank must classify |
| 177 | (f) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '177'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=177 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 180 | (i)  Commercial banks | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=True; attribute_eq={'tenor': 'call'} |  |
| 181 | (ii) Discount house | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; counterparty_types=['NBFI']; resident=True; attribute_eq={'institution_class': 'discount_house', 'tenor': 'call'} |  |
| 182 | (iii) Others | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; resident=True; attribute_eq={'tenor': 'call', 'institution_class': 'other'} |  |
| 183 | (b) Other financial institutions | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; counterparty_types=['NBFI']; resident=True; attribute_eq={'tenor': 'call', 'institution_class': 'other_financial'} |  |
| 186 | (i) Repos Payable | mapped | `positions.sum` position_types=['OTHER_LIABILITY']; attribute_eq={'instrument': 'repo_payable'} |  |
| 187 | (ii) Swaps Payable | mapped | `positions.sum` position_types=['FX_HEDGE', 'DERIVATIVE']; attribute_eq={'leg': 'payable'} |  |
| 188 | (iii) Others | input_required |  | other secured borrowings — bank must supply |
| 190 | (i) Commercial banks | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; resident=True; attribute_eq={'instrument': 'term_borrowing'} |  |
| 191 | (ii) Others | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; resident=True; attribute_eq={'instrument': 'term_borrowing', 'institution_class': 'other'} |  |
| 192 | (c) Other financial institutions | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; counterparty_types=['NBFI']; resident=True; attribute_eq={'instrument': 'term_borrowing'} |  |
| 193 | (d) Government | mapped | `positions.sum` position_types=['INTERBANK_BORROWING', 'OTHER_LIABILITY']; counterparty_types=['SOVEREIGN']; attribute_eq={'instrument': 'term_borrowing'} |  |
| 194 | (e) Others | input_required |  | other term borrowings — bank must classify |
| 195 | (f) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '195'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=195 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 198 | (i) Bank of Ghana | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 200 | -Commercial Banks | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 201 | -Discount houses | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 202 | -Others | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 203 | (iii) Other Financial Institutions | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 204 | (iv) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '204'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=204 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 206 | (i) Bank of Ghana | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 208 | -Commercial Banks | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 209 | -Others | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 210 | (iii) Other Finanacial Institutions | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 211 | (iv) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '211'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=211 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 213 | (i) Bank of Ghana | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 215 | -Commercial Banks | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 216 | -Others | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 217 | (iii) Other Financial Institutions | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 218 | (iv) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '218'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=218 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 220 | (i) Bank of Ghana | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 222 | -Commercial Banks | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 223 | -Others | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 224 | (iii) Other Financial Institutions | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 225 | (iv) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '225'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=225 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 228 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'CURRENT', 'block': '26'} |  |
| 229 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'CURRENT', 'block': '26'} |  |
| 230 | (iii) Central government | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['SOVEREIGN']; attribute_eq={'deposit_account_type': 'CURRENT'} |  |
| 231 | (iv) Public enterprises (Annex 13) | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'CURRENT', 'depositor_class': 'public_enterprise'} |  |
| 232 | (v) Public Institutions | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'CURRENT', 'depositor_class': 'public_institution'} |  |
| 233 | (vi) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'CURRENT', 'block': '26'} |  |
| 234 | (vii) Acrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '234'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=234 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 236 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'SAVINGS', 'block': '26'} |  |
| 237 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'SAVINGS', 'block': '26'} |  |
| 238 | (iii) Central government | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['SOVEREIGN']; attribute_eq={'deposit_account_type': 'SAVINGS'} |  |
| 239 | (iv) Public enterprises (Annex 13) | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'SAVINGS', 'depositor_class': 'public_enterprise'} |  |
| 240 | (v) Public Institutions | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'SAVINGS', 'depositor_class': 'public_institution'} |  |
| 241 | (vi) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'SAVINGS', 'block': '26'} |  |
| 242 | (vii) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '242'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=242 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 244 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'FIXED', 'block': '26'} |  |
| 245 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'FIXED', 'block': '26'} |  |
| 246 | (iii) Central government | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['SOVEREIGN']; attribute_eq={'deposit_account_type': 'FIXED'} |  |
| 247 | (iv) Public enterprises (Annex 13) | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'FIXED', 'depositor_class': 'public_enterprise'} |  |
| 248 | (v) Public Institutions | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'FIXED', 'depositor_class': 'public_institution'} |  |
| 249 | (vi) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'FIXED', 'block': '26'} |  |
| 250 | (vii) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '250'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=250 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 252 | (i) Individual | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'deposit_account_type': 'CALL', 'block': '26'} |  |
| 253 | (ii) Private enterprises | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['CORPORATE', 'SME']; attribute_eq={'deposit_account_type': 'CALL', 'block': '26'} |  |
| 254 | (iii) Central government | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['SOVEREIGN']; attribute_eq={'deposit_account_type': 'CALL'} |  |
| 255 | (iv) Public enterprises (Annex 13) | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'CALL', 'depositor_class': 'public_enterprise'} |  |
| 256 | (v) Public Institutions | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['GOVERNMENT_ENTITY']; attribute_eq={'deposit_account_type': 'CALL', 'depositor_class': 'public_institution'} |  |
| 257 | (vi) Others | mapped | `positions.sum` position_types=['DEPOSIT']; counterparty_types=['OTHER', 'NBFI']; attribute_eq={'deposit_account_type': 'CALL', 'block': '26'} |  |
| 258 | (vii) Accrued interest | mapped | `refs.sum` kind=interest_accruals; value_field=accrued_interest_ghs; filters={'bsd2_row': '258'}; currency_field=currency | accrued interest — Σ interest_accruals rows tagged bsd2_row=258 (accruals sub-ledger; Domestic/Foreign by row currency) |
| 261 | (i) Individual | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 262 | (ii) Private enterprises | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 263 | (iii) Central government | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 264 | (iv) Public enterprises | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 265 | (v) Public Institutions | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 266 | (vi) Others | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 268 | (i) Individual | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 269 | (ii) Private enterprises | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 270 | (iii) Central government | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 271 | (iv) Public enterprises | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 272 | (v) Public Institutions | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 273 | (vi) Others | coa-mapping |  | bank-specific chart-of-accounts mapping table required to split this line |
| 275 | (i) TOR margin account | mapped | `positions.sum` position_types=['DEPOSIT']; attribute_eq={'instrument': 'tor_margin_account'} |  |
| 276 | (ii) Others (Specify - Annex 14) | input_required |  | other margin accounts — Annex 14 detail required |
| 277 | 28. Bonds issued | mapped | `positions.sum` position_types=['OTHER_LIABILITY']; attribute_eq={'instrument': 'bond_issued'} |  |
| 278 | 29. Other liabilities  (Annex 15) | mapped | `positions.sum` position_types=['OTHER_LIABILITY'] |  |
| 282 | 33. Contingent liabilities (Annex 16) | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional |  |
| 283 | 34. Managed funds (contra) | input_required |  | managed funds (contra) — fiduciary book, outside own-books returns |

**Totals:** 205 leaf rows bound — 167 mapped, 38 input_required/coa-mapping (as of the 2026-08-16 data-gap closures: accrued interest, fixed assets by class, loan attributes; recount with the snippet in §Regenerating). Every roll-up (▲), the TOTAL column and `BSD2-Summary` are the template's own formulas, evaluated over these inputs.

## Annex sheets — line map (Wave 1b)

Generated from `bog_forms/linemaps/bsd2.py` (`_annex_lines()`) + `layouts/BSD2.json` — regenerate, do not hand-edit.

**How the annexes were bound.** `BSD2-Summary` (97 formulas ← BSD2) has no input cell — nothing to bind. Only
`BSD2-Annex 2a` (5) and `BSD2-Annex 4 ` (25) ship numeric placeholders, so only they are captured as `input`
cells in the layout and bound with `leaf_lines`; **every other annex is a BLANK detail schedule** (name / amount
rows with no `0` placeholder). Their official grids are declared explicitly with `grid_lines` — data rows read
from each sheet's own column headers and labels (first data row under the header rows, last data row at the
schedule's "Total" label or the last labelled item + its blank detail rows); nothing outside those rows is bound.

- **Detail rows** are `input_required` with the note *"… listing row N … from position-level data in a later
  wave"* — the per-counterparty / per-security / per-borrowing schedule that only position-level rendering can
  fill (name + amount [+ rate + maturity]); the official structure is exported in full and every such cell is
  listed on the Completion-notes sheet.
- **Schedule TOTAL rows** are bound only where the SAME declaration BSD2 uses for the line the annex analyses
  fills the total honestly, so *annex total = spine line by construction* (proved by
  `tests/services/bog_forms/test_bsd2_annexes.py`): Annex 6 ↔ line 11 other assets (`facts balance_sheet
  other_assets`, Domestic B / Foreign C), Annex 7 ↔ 15(e) other reserves, Annex 15 ↔ line 29 other liabilities
  (`positions OTHER_LIABILITY`, Domestic C / Foreign D), Annex 16 rows ↔ line 33 (`I11` = BSD2 `D282`).
  Where BSD2's own line is multi-source or itself input_required / coa-mapping (borrowings Annexes 8–11, Annex 12
  deposits of FIs, Annex 14 other margins, Annex 1 totals) the total row is `input_required` with the note
  *"schedule total — the template carries no formula here; Σ of the rows above"*.
- **Per-row "Total" COLUMNS** (Annex 6 `D`, Annex 14 `D`, Annex 15 `E`) and Annex 16 `C11:H11` carry no template
  formula; they are derived cells, not inputs, and are left to the bank's arithmetic (framework ask below).
- **Attribute conventions declared by this map** (snapshot `attributes` keys, like the spine's `instrument` /
  `institution_class`): Annex 4 `facility_type` ∈ {`scheduled`, `unscheduled`, `overdraft`, `acceptance`,
  `other`} (Guide Annex 4 definitions) and `scheme = staff_advance` for the "of which staff advances" row;
  Annex 16 `obs_category` ∈ {`acceptance`, `letter_of_credit`, `guarantee`, `endorsement`, `other_obligation`}
  (extends the LMT engine's existing `obs_category` values `letter_of_credit` / `guarantee`) × `obs_status` ∈
  {`performing`, `non_performing`}. Annex 4 `G13` = BSD2 `D68` and Annex 16 `I11` = BSD2 `D282` hold exactly
  when those attributes partition the LOAN / LC_GUARANTEE books; positions without them fall out of the annex
  (never out of BSD2).

### `BSD2-Annex 1` — Breakdown of foreign assets and foreign liabilities (Guide Annex 1)

45 declared cells (0 captured numeric inputs in the template, 45 blank-grid cells) · 0 template formulas · **4 mapped · 41 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 4 | C/D | 1. Foreign Currency Notes and Coins | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 4 | E | 1. Foreign Currency Notes and Coins | mapped | `positions.sum` position_types=['CASH']; attribute_eq={'instrument': 'fx_notes_coins'}; currency=FX |  |
| 5–10 | C/D | (detail rows) | input_required |  | detail schedule row N — populated from position-level data in a later wave |
| 5–10 | E | (detail rows) | input_required |  | detail row N — per-currency breakdown of foreign currency notes and coins; populated from position-level data in a later wave |
| 11 | C/D | 2. Balances with non-resident banks including cheques, remittances, etc | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 11 | E | 2. Balances with non-resident banks including cheques, remittances, etc | mapped | `positions.sum` position_types=['INTERBANK_PLACEMENT', 'CASH']; resident=False; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; currency=FX |  |
| 12 | C/D | 3. Foreign Bills | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 12 | E | 3. Foreign Bills | input_required |  | foreign bills — split of BSD2 A.3 other claims on non-residents; bank must supply |
| 13 | C/D | 4. Other | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 13 | E | 4. Other | input_required |  | other foreign assets — bank must supply |
| 14 | C/D | Total | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 14 | E | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 A. FOREIGN ASSETS) |
| 19 | C/D | 1. Balances including cheques, remittances etc due to non-resident banks | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 19 | E | 1. Balances including cheques, remittances etc due to non-resident banks | mapped | `positions.sum` position_types=['DEPOSIT']; resident=False; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; currency=FX |  |
| 20 | C/D | 2. Borrowings from non-resident banks | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 20 | E | 2. Borrowings from non-resident banks | mapped | `positions.sum` position_types=['INTERBANK_BORROWING']; resident=False; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; currency=FX |  |
| 21 | C/D | 3. Other | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 21 | E | 3. Other | input_required |  | other foreign liabilities — bank must supply |
| 22 | C/D | Total | input_required |  | foreign currency amount and exchange rate — per-currency detail from position-level data in a later wave |
| 22 | E | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 C. FOREIGN LIABILITIES) |

### `BSD2-Annex 2a` — Bank of Ghana account reconciliation (Guide Annex 2)

30 declared cells (5 captured numeric inputs in the template, 25 blank-grid cells) · 1 template formulas · **0 mapped · 30 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 10 | C | Balance per Bank of Ghana statement as at | input_required |  | balance per Bank of Ghana statement — external (BoG statement); bank must supply |
| 12 | C | Add: Lodgement not credited by Bank of Ghana | input_required |  | reconciling item — bank's BoG account reconciliation; bank must supply |
| 13 | C | Withdrawals not debited by reporting institution | input_required |  | reconciling item — bank's BoG account reconciliation; bank must supply |
| 15 | C | Less: Lodgement credited by Bank of Ghana but not by reporting institution | input_required |  | reconciling item — bank's BoG account reconciliation; bank must supply |
| 16 | C | Withdrawals debited by bank but not by Bank of Ghana | input_required |  | reconciling item — bank's BoG account reconciliation; bank must supply |
| 26–50 | C | (detail rows) | input_required |  | reconciling items over 1 month old — listing row N (date / description / amount); bank must supply |

### `BSD2-Annex 2b` — List of special deposits (Guide Annex 2)

43 declared cells (0 captured numeric inputs in the template, 43 blank-grid cells) · 0 template formulas · **0 mapped · 43 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6–48 | B | (detail rows) | input_required |  | special deposit — listing row N (description / amount) from position-level data in a later wave |

### `BSD2-Annex 2c` — List of swap deals receivable (Guide Annex 2)

43 declared cells (0 captured numeric inputs in the template, 43 blank-grid cells) · 0 template formulas · **0 mapped · 43 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6–48 | B | (detail rows) | input_required |  | swap deal receivable — listing row N (description / amount) from position-level data in a later wave |

### `BSD2-Annex 2d` — List of repos receivable (Guide Annex 2)

43 declared cells (0 captured numeric inputs in the template, 43 blank-grid cells) · 0 template formulas · **0 mapped · 43 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6–48 | B | (detail rows) | input_required |  | repo receivable — listing row N (description / amount) from position-level data in a later wave |

### `BSD2-Annex 3 ` — Detailed list of short-term investments, ≤ 1 year (Guide Annex 3)

88 declared cells (0 captured numeric inputs in the template, 88 blank-grid cells) · 0 template formulas · **0 mapped · 88 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6–49 | B/C | (detail rows) | input_required |  | short-term investment (≤ 1 year) — listing row N (description / nominal / book value) from SECURITY_HOLDING positions in a later wave |

### `BSD2-Annex 4 ` — Analysis of loans, overdrafts and other advances by facility type (Guide Annex 4)

25 declared cells (25 captured numeric inputs in the template, 0 blank-grid cells) · 17 template formulas · **15 mapped · 10 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 8 | B | A. Government and Public Enterprises / Institutions | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['SOVEREIGN', 'GOVERNMENT_ENTITY']; attribute_eq={'facility_type': 'scheduled'}; currency=all |  |
| 8 | C | A. Government and Public Enterprises / Institutions | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['SOVEREIGN', 'GOVERNMENT_ENTITY']; attribute_eq={'facility_type': 'unscheduled'}; currency=all |  |
| 8 | D | A. Government and Public Enterprises / Institutions | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['SOVEREIGN', 'GOVERNMENT_ENTITY']; attribute_eq={'facility_type': 'overdraft'}; currency=all |  |
| 8 | E | A. Government and Public Enterprises / Institutions | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['SOVEREIGN', 'GOVERNMENT_ENTITY']; attribute_eq={'facility_type': 'acceptance'}; currency=all |  |
| 8 | F | A. Government and Public Enterprises / Institutions | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['SOVEREIGN', 'GOVERNMENT_ENTITY']; attribute_eq={'facility_type': 'other'}; currency=all |  |
| 9 | B | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on public-sector advances by facility type — provisions sub-ledger required |
| 9 | C | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on public-sector advances by facility type — provisions sub-ledger required |
| 9 | D | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on public-sector advances by facility type — provisions sub-ledger required |
| 9 | E | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on public-sector advances by facility type — provisions sub-ledger required |
| 9 | F | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on public-sector advances by facility type — provisions sub-ledger required |
| 10 | B | B. Private Enterprises and Individual | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['CORPORATE', 'SME', 'RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'scheduled'}; currency=all |  |
| 10 | C | B. Private Enterprises and Individual | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['CORPORATE', 'SME', 'RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'unscheduled'}; currency=all |  |
| 10 | D | B. Private Enterprises and Individual | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['CORPORATE', 'SME', 'RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'overdraft'}; currency=all |  |
| 10 | E | B. Private Enterprises and Individual | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['CORPORATE', 'SME', 'RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'acceptance'}; currency=all |  |
| 10 | F | B. Private Enterprises and Individual | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['CORPORATE', 'SME', 'RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'other'}; currency=all |  |
| 11 | B | (of which staff advances) | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'scheduled', 'scheme': 'staff_advance'}; currency=all |  |
| 11 | C | (of which staff advances) | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'unscheduled', 'scheme': 'staff_advance'}; currency=all |  |
| 11 | D | (of which staff advances) | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'overdraft', 'scheme': 'staff_advance'}; currency=all |  |
| 11 | E | (of which staff advances) | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'acceptance', 'scheme': 'staff_advance'}; currency=all |  |
| 11 | F | (of which staff advances) | mapped | `positions.sum` position_types=['LOAN']; counterparty_types=['RETAIL_INDIVIDUAL']; attribute_eq={'facility_type': 'other', 'scheme': 'staff_advance'}; currency=all |  |
| 12 | B | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on private-sector advances by facility type — provisions sub-ledger required |
| 12 | C | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on private-sector advances by facility type — provisions sub-ledger required |
| 12 | D | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on private-sector advances by facility type — provisions sub-ledger required |
| 12 | E | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on private-sector advances by facility type — provisions sub-ledger required |
| 12 | F | Less: Bad debt provisions and interest suspense | input_required |  | bad-debt provisions and interest in suspense on private-sector advances by facility type — provisions sub-ledger required |

### `BSD2-Annex 5 ` — Detailed list of long-term investments, > 1 year (Guide Annex 5)

88 declared cells (0 captured numeric inputs in the template, 88 blank-grid cells) · 0 template formulas · **0 mapped · 88 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6–49 | B/C | (detail rows) | input_required |  | long-term investment (> 1 year) — listing row N (description / nominal / book value) from SECURITY_HOLDING positions in a later wave |

### `BSD2-Annex 6` — Analysis of other assets (Guide Annex 6)

90 declared cells (0 captured numeric inputs in the template, 90 blank-grid cells) · 0 template formulas · **2 mapped · 88 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 7–50 | B/C | (detail rows) | input_required |  | other asset — listing row N (description / domestic / foreign) from position-level data in a later wave |
| 51 | B/C | Total | mapped | `facts.sum` group=balance_sheet; categories=['other_assets'] |  |

### `BSD2-Annex7` — List of other reserves (Guide Annex 7)

45 declared cells (0 captured numeric inputs in the template, 45 blank-grid cells) · 0 template formulas · **1 mapped · 44 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6–49 | C | (detail rows) | input_required |  | other reserve — listing row N (type / amount); bank must supply |
| 50 | C | Total | mapped | `facts.sum` group=capital_component; categories=['other_reserves']; currency=all |  |

### `BSD2-Annex 8` — Analysis of short-term borrowings — foreign (Guide Annex 8)

177 declared cells (0 captured numeric inputs in the template, 177 blank-grid cells) · 0 template formulas · **0 mapped · 177 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 7–50 | B/C/D/E | (detail rows) | input_required |  | short-term foreign borrowing — listing row N (source / currency amount / cedi equivalent / rate / maturity) from INTERBANK_BORROWING positions in a later wave |
| 51 | C | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 line 18) |

### `BSD2-Annex 9` — Analysis of long-term borrowings — foreign (Guide Annex 9)

177 declared cells (0 captured numeric inputs in the template, 177 blank-grid cells) · 0 template formulas · **0 mapped · 177 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 7–50 | B/C/D/E | (detail rows) | input_required |  | long-term foreign borrowing — listing row N (source / currency amount / cedi equivalent / rate / maturity) from INTERBANK_BORROWING positions in a later wave |
| 51 | C | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 line 19) |

### `BSD2-Annex 10` — Analysis of long-term borrowings — domestic (Guide Annex 10)

133 declared cells (0 captured numeric inputs in the template, 133 blank-grid cells) · 0 template formulas · **0 mapped · 133 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 7–50 | B/C/D | (detail rows) | input_required |  | long-term domestic borrowing — listing row N (source / amount / rate / maturity) from INTERBANK_BORROWING positions in a later wave |
| 51 | B | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 line 21) |

### `BSD2-Annex 11` — Analysis of short-term borrowings — domestic (Guide Annex 11)

133 declared cells (0 captured numeric inputs in the template, 133 blank-grid cells) · 0 template formulas · **0 mapped · 133 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 7–50 | B/C/D | (detail rows) | input_required |  | short-term domestic borrowing — listing row N (source / amount / rate / maturity) from INTERBANK_BORROWING positions in a later wave |
| 51 | B | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 line 23) |

### `BSD2-Annex 12` — Deposits of financial institutions (Guide Annex 12)

47 declared cells (0 captured numeric inputs in the template, 47 blank-grid cells) · 0 template formulas · **0 mapped · 47 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 8–17 | C | (detail rows) | input_required |  | deposit of a financial institution (Bank of Ghana) — listing row N (name / amount) from DEPOSIT positions in a later wave |
| 19–30 | C | (detail rows) | input_required |  | deposit of a financial institution (commercial banks) — listing row N (name / amount) from DEPOSIT positions in a later wave |
| 32–43 | C | (detail rows) | input_required |  | deposit of a financial institution (other banks) — listing row N (name / amount) from DEPOSIT positions in a later wave |
| 45–56 | C | (detail rows) | input_required |  | deposit of a financial institution (other financial institutions) — listing row N (name / amount) from DEPOSIT positions in a later wave |
| 57 | C | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 line 24, itself awaiting the bank's chart-of-accounts mapping) |

### `BSD2-Annex 13` — Public enterprises deposits (Guide Annex 13)

38 declared cells (0 captured numeric inputs in the template, 38 blank-grid cells) · 0 template formulas · **0 mapped · 38 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 8–15 | B | (detail rows) | input_required |  | public enterprise demand deposits — listing row N (name of enterprise / amount) from DEPOSIT positions (GOVERNMENT_ENTITY, depositor_class = public_enterprise) in a later wave |
| 17–25 | B | (detail rows) | input_required |  | public enterprise savings accounts — listing row N (name of enterprise / amount) from DEPOSIT positions (GOVERNMENT_ENTITY, depositor_class = public_enterprise) in a later wave |
| 27–37 | B | (detail rows) | input_required |  | public enterprise time deposits — listing row N (name of enterprise / amount) from DEPOSIT positions (GOVERNMENT_ENTITY, depositor_class = public_enterprise) in a later wave |
| 39–48 | B | (detail rows) | input_required |  | public enterprise certificates of deposit — listing row N (name of enterprise / amount) from DEPOSIT positions (GOVERNMENT_ENTITY, depositor_class = public_enterprise) in a later wave |

### `BSD2-Annex 14` — Analysis of other margins against contingent liabilities (Guide Annex 14)

90 declared cells (0 captured numeric inputs in the template, 90 blank-grid cells) · 0 template formulas · **0 mapped · 90 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 7–50 | B/C | (detail rows) | input_required |  | other margin against contingent liabilities — listing row N (description / domestic / foreign) from position-level data in a later wave |
| 51 | B/C | Total | input_required |  | schedule total — the template carries no formula here; Σ of the rows above (same-sheet sums are a framework ask) (= BSD2 27(ii), itself input_required) |

### `BSD2-Annex 15` — Analysis of other liabilities (Guide Annex 15)

92 declared cells (0 captured numeric inputs in the template, 92 blank-grid cells) · 0 template formulas · **2 mapped · 90 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6–50 | C/D | (detail rows) | input_required |  | other liability — listing row N (description / domestic / foreign) from OTHER_LIABILITY positions in a later wave |
| 51 | C/D | Total | mapped | `positions.sum` position_types=['OTHER_LIABILITY'] |  |

### `BSD2-Annex 16` — Statement of contingent liabilities (Guide Annex 16)

30 declared cells (0 captured numeric inputs in the template, 30 blank-grid cells) · 6 template formulas · **20 mapped · 10 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 6 | C/D | 1. Liabilities on acceptances | input_required |  | foreign currency amount and conversion rate — per-currency support schedule (Guide Annex 16); bank must supply |
| 6 | E | 1. Liabilities on acceptances | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'acceptance', 'obs_status': 'performing'}; currency=FX |  |
| 6 | F | 1. Liabilities on acceptances | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'acceptance', 'obs_status': 'non_performing'}; currency=FX |  |
| 6 | G | 1. Liabilities on acceptances | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'acceptance', 'obs_status': 'performing'}; currency=GHS |  |
| 6 | H | 1. Liabilities on acceptances | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'acceptance', 'obs_status': 'non_performing'}; currency=GHS |  |
| 7 | C/D | 2. Liabilities on documentary credits | input_required |  | foreign currency amount and conversion rate — per-currency support schedule (Guide Annex 16); bank must supply |
| 7 | E | 2. Liabilities on documentary credits | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'letter_of_credit', 'obs_status': 'performing'}; currency=FX |  |
| 7 | F | 2. Liabilities on documentary credits | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'letter_of_credit', 'obs_status': 'non_performing'}; currency=FX |  |
| 7 | G | 2. Liabilities on documentary credits | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'letter_of_credit', 'obs_status': 'performing'}; currency=GHS |  |
| 7 | H | 2. Liabilities on documentary credits | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'letter_of_credit', 'obs_status': 'non_performing'}; currency=GHS |  |
| 8 | C/D | 3. Liabilities on guarantees | input_required |  | foreign currency amount and conversion rate — per-currency support schedule (Guide Annex 16); bank must supply |
| 8 | E | 3. Liabilities on guarantees | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'guarantee', 'obs_status': 'performing'}; currency=FX |  |
| 8 | F | 3. Liabilities on guarantees | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'guarantee', 'obs_status': 'non_performing'}; currency=FX |  |
| 8 | G | 3. Liabilities on guarantees | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'guarantee', 'obs_status': 'performing'}; currency=GHS |  |
| 8 | H | 3. Liabilities on guarantees | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'guarantee', 'obs_status': 'non_performing'}; currency=GHS |  |
| 9 | C/D | 4. Liabilities on endorsements | input_required |  | foreign currency amount and conversion rate — per-currency support schedule (Guide Annex 16); bank must supply |
| 9 | E | 4. Liabilities on endorsements | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'endorsement', 'obs_status': 'performing'}; currency=FX |  |
| 9 | F | 4. Liabilities on endorsements | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'endorsement', 'obs_status': 'non_performing'}; currency=FX |  |
| 9 | G | 4. Liabilities on endorsements | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'endorsement', 'obs_status': 'performing'}; currency=GHS |  |
| 9 | H | 4. Liabilities on endorsements | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'endorsement', 'obs_status': 'non_performing'}; currency=GHS |  |
| 10 | C/D | 5. Liabilities on other obligations | input_required |  | foreign currency amount and conversion rate — per-currency support schedule (Guide Annex 16); bank must supply |
| 10 | E | 5. Liabilities on other obligations | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'other_obligation', 'obs_status': 'performing'}; currency=FX |  |
| 10 | F | 5. Liabilities on other obligations | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'other_obligation', 'obs_status': 'non_performing'}; currency=FX |  |
| 10 | G | 5. Liabilities on other obligations | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'other_obligation', 'obs_status': 'performing'}; currency=GHS |  |
| 10 | H | 5. Liabilities on other obligations | mapped | `positions.sum` position_types=['LC_GUARANTEE']; measure=notional; attribute_eq={'obs_category': 'other_obligation', 'obs_status': 'non_performing'}; currency=GHS |  |

### `BSD2-Annex 17` — Number of customers by type (Guide Annex 17)

8 declared cells (0 captured numeric inputs in the template, 8 blank-grid cells) · 0 template formulas · **0 mapped · 8 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 5 | C/D | Demand | input_required |  | number of customers and number of accounts by deposit type — a count over DEPOSIT positions per counterparty (counting resolver is a framework ask); unscaled count |
| 7 | C/D | Savings | input_required |  | number of customers and number of accounts by deposit type — a count over DEPOSIT positions per counterparty (counting resolver is a framework ask); unscaled count |
| 9 | C/D | Time | input_required |  | number of customers and number of accounts by deposit type — a count over DEPOSIT positions per counterparty (counting resolver is a framework ask); unscaled count |
| 11 | C/D | Certificate of Deposits | input_required |  | number of customers and number of accounts by deposit type — a count over DEPOSIT positions per counterparty (counting resolver is a framework ask); unscaled count |

**Annex totals:** 1465 declared cells — 44 mapped, 1421 input_required, 0 coa-mapping.

### Residual — data the bank must supply (annexes)

- Every detail / listing row of Annexes 1 (per-currency rows), 2a (reconciling items > 1 month), 2b–2d, 3, 5,
  6, 7, 8–11, 12, 13, 14, 15 — position-level rendering (later wave) or the bank's sub-ledgers (reconciliation,
  reserves register).
- Annex 2a reconciliation cells `C10:C16` — the Bank of Ghana statement balance and reconciling items are external
  to the platform.
- Annex 1 / 8 / 9 / 16 foreign-currency amount and exchange-rate columns — per-currency detail; canonical
  `positions.sum` cannot split one row by currency.
- Annex 4 rows 9 / 12 (bad-debt provisions + interest in suspense by borrower class × facility type) — provisions
  sub-ledger.
- Annex 17 counts of customers / accounts by deposit type — needs a counting resolver (framework ask).
- Schedule totals of Annexes 1, 8–12, 14 (see above).

### Framework asks (Wave 1b, annexes)

1. **Same-sheet / same-form sums.** Blank-schedule "Total" rows and per-row "Total" columns have no template
   formula; a `form.self_sum {sheet, refs|range}` resolver (or letting `form.cell` read the form being computed
   after its own inputs resolve) would let the annex totals be derived from the schedule rows once those are
   populated, instead of `input_required`.
2. **`positions.sum` cedi equivalents.** The resolver sums `snapshot.balance`, which the canonical model holds in
   the position's NATIVE currency (the cedi value is `attributes["balance_ghs"]`, the convention
   `fact_derivation` / the LMT engine use). For FOREIGN columns (BSD2 `C`, Annex 1 `E`, Annex 16 `E/F`, BSD2A) it
   should sum `balance_ghs` (falling back to `balance` for base-currency positions); today a mixed USD/GBP/EUR
   book adds native amounts.
3. **Counting resolver** (`positions.count {…, distinct: counterparty|position}`) for Annex 17 (and BSD3's
   depositor counts).
4. **Row-count of the official grids.** Blank-schedule extents (e.g. Annex 1 rows 5–10 read as per-currency
   detail rows; BSD2A rows 106–127 treated as outside the grid) are read from labels/headers — a `grid` hint in
   the layout JSON (first/last data row per sheet, from the template's borders) would make that non-judgemental.

## Data-gap closure — accrued interest (2026-08-16)

The 19 "Accrued interest" lines (rows 20, 29, 32, 141, 145, 151, 156, 161, 166, 177, 195, 204, 211, 218, 225,
234, 242, 250, 258 — 38 input cells, Domestic `B` / Foreign `C`) are now fed from the bank's **accruals
sub-ledger**, reference dataset `interest_accruals` (`docs/data_engine/datasets/interest_accruals.md`;
schema `reference_schemas/interest_accruals.py`), ingested like every other reference dataset (app upload or
API push, `docs/API_INTEGRATION.md` §3.5). Binding: `linemaps/bsd2.py::accrued_interest(row, …)` →
`refs.sum {kind: interest_accruals, value_field: accrued_interest_ghs, filters: {bsd2_row: <row>},
currency_field: currency}`. The bank tags each accrual balance with **`bsd2_row` = the row number of the
"Accrued interest" line on the official BSD2 sheet** (the vocabulary table in the dataset doc names each row in
the template's own section/line words), plus `side` (asset | liability, checked against the row) and
`currency` — Domestic = the bank's base currency, Foreign = any other (Guide §2), applied by the new
`currency_field` option of `refs.sum`. Blank until the sub-ledger has been ingested at all; `0` for a tagged
line with no row; the TOTAL column stays the template's `=B+C`. Rows 204/211/218/225 were `coa-mapping`
placeholders inside the deposits-of-financial-institutions blocks and are the same accrual lines. Proof:
`tests/services/data_gaps/test_interest_accruals.py` (Sample Bank sub-ledger pushed through the real API →
all 38 cells `input_required` → `mapped`, expected values, TOTAL = B + C). Sample Bank onboarding data:
`backend/onboarding/sample_bank/interest_accruals.csv`.

## Data-gap closure — fixed assets by class (2026-08-16)

Item 12 rows 115–121 (property, plant and equipment **at cost** by class, and work-in-progress) and row 123
(accumulated depreciation) — 16 input cells, Domestic `B` / Foreign `C` — are now fed from the bank's
**fixed-asset / capital-expenditure register**, reference dataset `capital_expenditure`
(`docs/data_engine/datasets/capital_expenditure.md`; schema `reference_schemas/capital_expenditure.py`; the
same register fills all 50 BSD10 cells), ingested like every other reference dataset (app upload or API push,
`docs/API_INTEGRATION.md` §3.5). Binding: `linemaps/bsd2.py::fixed_assets(field, …, *classes)` → `refs.sum
{kind: capital_expenditure, value_field: <field>, filters: {asset_class: [...]}, currency_field: currency}`.
Rows 115–120 read `closing_cost_ghs` per class (`land_buildings`, `staff_land_premises`, `computers`,
`furniture_equipment` + `other_office_equipment`, `motor_vehicles`, `other_property_legal_rights`), row 121
Σ `wip_closing_ghs` over all classes, row 123 Σ `accumulated_depreciation_ghs` — **never the register's NBV**:
item 12 `=B122−B123` (sub-total of 115–121 less depreciation) is BoG's own arithmetic, so binding NBV at
115–121 would double-deduct depreciation; bound this way, item 12 equals the register's Σ NBV + Σ WIP by
construction. Domestic = rows booked in the bank's base currency (or with no `currency`), Foreign = any other
(Guide §2, `refs.sum currency_field`). Blank until the register has been ingested at all; `0` for a class the
register carries no row for; the TOTAL column stays the template's `=B+C`. Proof:
`tests/services/data_gaps/test_capital_expenditure.py` (Sample Bank register pushed through the real API →
the 16 cells `coa-mapping/input_required` → `mapped`, `SUM(115:121) − 123` = Σ NBV + Σ WIP, an earlier
period pushed later leaves the later period untouched). Sample Bank onboarding data:
`backend/onboarding/sample_bank/capital_expenditure*.csv`.

## Cross-form dependencies
- `BSD2-Summary` ← `BSD2` (in-workbook formulas)
- `BSD8!H22` = `[1]BSD2!D38+[1]BSD2!D39` (external link; resolved from the computed BSD2 of the same reporting date)
- `BSD6A/6B` — 'FROM BSD2' totals; `BSD2A` — analyses BSD2's FOREIGN column: its category rows read
  `BSD2!C7 … C278` and net worth `BSD2!D135` through `form.cell` / `bsd2a.form_cells_sum` (see
  `bsd2a_line_map.md`)


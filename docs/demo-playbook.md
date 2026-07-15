# AequorOS ten-minute demo playbook

This playbook starts from the pristine demo portfolio and uses only borrower names and screen
labels. Presenters never need to copy or expose an internal identifier.

## Before the meeting

Follow the root [Run the MVP locally](../README.md#run-the-mvp-locally) quickstart. Immediately
before the meeting, restore the portfolio and open the queue:

```bash
RISK_DEMO_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:15432/risk_service \
  mise run risk-service:reset-demo
```

Go to `http://127.0.0.1:5173/cases`. The reset is safe to repeat and affects only the fixed
`AequorOS Pan-African Demo Bank` tenant.

## The click path

| Time | Screen and exact clicks                                                                                                                                                                                                                           | Talking point                                                                                                                                                        |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0:00 | **Case Queue.** Clear any search filter. Point out **Volta Aluminium Industries Plc**, **Adom Textiles & Garments Ltd**, **Kivu Fresh Produce Logistics Ltd**, and **Baobab Health Distribution SA**.                                             | “The queue is a portfolio, not a toy case: it spans a clean large corporate, a correctable SME exception, scenario-driven liquidity stress, and a completed review.” |
| 0:45 | Open **Covenant exception — Adom Textiles & Garments Ltd**. Scan the six case-health summaries, then click **Covenants** to open the focused **Financial Workspace**.                                                                             | “Validation, scenarios, forecast, findings, covenant compliance, and the decision are visible at a glance; each summary opens its owning review surface.”            |
| 1:20 | Under **Unmapped source rows**, inspect the current-assets row. Under **Covenants**, open **Source** for **Minimum current ratio**.                                                                                                               | “The signed source says 1.24x, while an OCR column shift produced 0.91x; row-level lineage makes the apparent breach explainable.”                                   |
| 2:20 | On **Minimum current ratio**, click **Edit**. Set **Actual value** to `1.24`, leave compliance on automatic calculation, enter reason `Correct OCR column shift against signed Q2 covenant certificate`, and save.                                | “Canonical corrections require a reason, preserve source evidence, and recalculate compliance rather than silently overwriting the record.”                          |
| 3:20 | Open **Scenarios**, select **Downside — collections stress**, and scan the five reviewed assumptions.                                                                                                                                             | “The downside is explicit and reviewable: revenue, expenses, collection timing, credit usage, and repayment behavior all feed the calculation.”                      |
| 4:10 | Open **Forecast**. Select **Downside — collections stress** and click **Run forecast**. Inspect **Projected balance sheet outputs**.                                                                                                              | “A rerun takes the current canonical data and reviewed assumptions into a new immutable snapshot; the seeded history remains intact.”                                |
| 5:10 | In **Run history**, open the red **Failed** entry and expand its diagnostics. Then return to the latest successful entry.                                                                                                                         | “Failures are first-class audit records with corrective guidance, while prior valid outputs are preserved for review.”                                               |
| 6:00 | In **Current case**, open **Liquidity stress review — Kivu Fresh Produce Logistics Ltd**. Open **Liquidity**, select **Downside — collections stress**, and expand **Supporting evidence** on the highest-severity finding.                       | “The liquidity conclusion is generated from the forecast and links back to the exact period, canonical inputs, and reviewed assumptions.”                            |
| 7:00 | Open **Capital** and compare **Baseline** with **Downside — collections stress** by period.                                                                                                                                                       | “The same immutable Kivu forecast evidence drives the capital comparison, showing when balance-sheet pressure becomes a capital concern.”                            |
| 8:00 | In **Current case**, return to **Covenant exception — Adom Textiles & Garments Ltd**. Open **Decisions** and record **Needs more info** with reason `Obtain management confirmation of the corrected covenant certificate before committee.`      | “The human decision remains explicit and auditable even when calculations and evidence are automated.”                                                               |
| 9:00 | Use the current-case selector to open **Completed review — Baobab Health Distribution SA**. Open **Reports** and show the default HTML approval report and monitoring finding. The UUID-free JSON view remains available for technical audiences. | “The completed case closes the loop: source-backed findings and the reviewer’s decision become a committee-ready report.”                                            |

## Reset and repeat

Stop at ten minutes. To return every correction, decision, run, finding, and report input to the
same starting narrative, run the one reset command again and refresh the browser:

```bash
RISK_DEMO_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:15432/risk_service \
  mise run risk-service:reset-demo
```

The expected starting outcomes are:

- Volta Aluminium Industries is low risk with no open manual finding.
- Adom Textiles has a 0.91x extracted current ratio, one unmapped source row, a source-backed
  covenant finding, a successful baseline forecast, and a preserved failed downside attempt.
- Kivu Fresh Produce Logistics has positive minimum cash under baseline and negative minimum cash
  under downside, with pre-generated liquidity evidence and matched capital projections.
- Baobab Health Distribution is completed and approved, so its HTML report opens immediately and
  its UUID-free JSON view remains available from the toggle.

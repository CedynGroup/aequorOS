# Feature-owned property inputs and assertions

The backend helpers live in `tests/<feature>/testing/{generators,assertions}.py`.
Dashboard helpers live in `unit/<feature>/testing/{generators,assertions}.ts`.
They belong to the feature test package so Hypothesis, fast-check and assertion
libraries stay out of production imports. Cross-feature mechanics belong in
`tests/support`; keep financial assumptions and tolerances with their owner.

Each generator cites the schema, engine or regulatory source in its docstring.
Synthetic realistic inputs are the default; deliberately include valid edges and
name invalid/adversarial inputs separately. Generator self-tests execute public
validators or engines. Stateful initializer strategies are also exercised through
real commands: never install a generated status directly into a database row.

Filing calendar inputs use registry cadence and deadline metadata in Accra time,
including leap-year period ends and observations one second either side of a
cutoff. Governed ICAAP deadlines remain absent until a parameter is supplied;
midnight observations for date-only deadlines do not claim a regulatory cutoff.
Review histories use distinct synthetic officers and fresh decisions after returns.
The service machines retain unauthorized actors, stale digests and illegal actions.

Assertion modules name the business invariant and report the relevant evidence.
They compare outputs or relate runs; they must not implement the engine formula.
Filing, ICAAP freeze and governed approvals compare evidence exactly: their
`TOLERANCE` policies are zero. New financial domains use `TolerancePolicy` with
an absolute Decimal tolerance, units and a source citation; choose the policy from
the engine's actual rounding, never an arbitrary epsilon. Dashboard filing uses
an equivalent exact `filingTolerance` policy.

Metamorphic checks here cover decision-order independence, officer renaming and
selected-bank authority under capability reordering or sibling changes. Financial
scaling, zero-exposure and currency round trips belong with the financial domains.
Do not carry monetary tolerance into status, identity or cryptographic comparisons.

Keep the existing CI budgets and regression coverage documented in the
[backend workflow guide](../../docs/state_machine_testing.md#bounded-reproducible-execution)
and [dashboard testing guide](../../dashboard/README.md#unit-test-collection).
The [shared testing standard](../../../CODEBASE_CONVENTIONS.md#behavior-properties-and-state-machines-backend-and-dashboard)
governs both surfaces.

Issue #553 has an approved five-PR sequence: scaffolding plus filing/ICAAP/approvals
then liquidity plus capital, credit plus FX, FTP plus IRRBB plus
canonical data, then testing-standard documentation.

## Liquidity and capital

Liquidity and capital helpers live in their feature-owned `testing` packages on
both surfaces. Backend facts use the engines' four-decimal money quantum and
six-decimal percentage quantum, with `ROUND_HALF_UP`. Each domain's ratio
comparison policy allows two percentage quanta for independently rounded runs;
identity and order comparisons remain exact. Dashboard liquidity comparisons
allow one money quantum when accumulating parsed Numbers; capital display
fixtures use exact hundredths at the component's two-decimal display precision.

Backend generators consume the parameter register and regulatory seed catalogue.
The bank CAR seed includes the conservation buffer; LCR/NSFR targets are internal
monitoring levels, not BoG requirements. Capital books reconcile the balance
sheet and include the three-year BIA input series. Liquidity books preserve
balance-sheet and granular-loan tie-outs while the public engine calibrates the
funding mix toward the NSFR target. Ladder totals come from five nonnegative
asset/liability buckets; net gaps can be negative. All data is synthetic.

Validity properties execute the public engines or dashboard parser. Metamorphic
assertions compare scaling, reordering and zero-exposure results without copying
production ratio formulas. Keep malformed/missing-value examples alongside the
valid defaults: decoder tests still verify that persisted figures are presented
without recomputation, including deliberate mismatches and signed-value edges.

## Credit and FX

Credit books use the control-plane bank five-grade classification grid. DPD
inputs mix performing loans with grade-boundary neighbors; IFRS 9 stages and booked provisions
remain consistent with classification. Zero facilities are deliberate edges.
The public classification engine supplies provisions, so the generator does
not duplicate its formula. Connected-group examples split facilities around
the seeded large-exposure limit, supplied explicitly as a synthetic Board
limit to the concentration monitor. That monitor does not invent a limit.

Credit money is four-decimal, and its six-decimal NPL ratio is a fraction,
not a percentage. Its policy distinguishes NPL fractions from concentration percentage points.
Classification uses Decimal's HALF_EVEN quantization. FX
uses four-decimal HALF_UP money and six-decimal percentage ratios. Its one
policy names currency and ratio tolerances separately so unlike units cannot
share an epsilon. FX spot bands surround hand-constructed golden fixtures,
not current market quotes. Currency legs reconcile and round-trip through
the reporting currency; open positions favor source-register limit neighbors.

Dashboard credit flows match synthetic facility counts and amounts. Realistic PD
estimates pair observed defaults and loan-months with an independently compounded
annual PD, rounded to two decimal places in percentage points. The consistency assertion
allows half that display quantum for annual PD; displayed figures still compare
exactly. Saved analytical estimates, FX nets and verdicts retain explicitly named
adversarial inputs: presentation must preserve saved observations even when
independent values disagree. Existing malformed/missing cases remain alongside
valid defaults. Status and identity are exact.

## FTP, IRRBB and canonical data

FTP's feature-owned books align products to a monotone synthetic transfer curve
through the public engine. Monotonicity is a fixture assumption; the engine also
accepts inverted curves, which retain their golden tests. NMD shares favour the
bank policy band's endpoints and neighbours. `BANK_FTP_THRESHOLDS` supplies
pricing caps and the core band: these are internal policy, not regulatory minima.
Money uses four-decimal HALF_UP and percentages six-decimal HALF_UP. The one
FTP tolerance policy names both units; sums allow accumulated independent rounding.
Dashboard defaults have coherent margins/contributions; independent stored-value
adversaries remain separate so presentation never silently reprices saved figures.

IRRBB strategies share the existing governed Standardised Framework parameter
fixture and its public parser. Positive principal is an asset and negative
principal is funding; each schedule's outstanding matches its remaining principal.
Synthetic zero-coupon maturities generate repricing differences under the standard
shock set, with capital biased around the parsed outlier threshold. The fixture's
15% threshold and calibration remain pending exposure-draft data. Mixed-sign and
negative-rate adversaries remain explicit. SF money and percentage output uses
six-place HALF_UP; the ICAAP bridge uses four-place amounts. One IRRBB policy keeps
these units distinct and preserves the existing much tighter behavioural-survival
conservation budget. Metamorphic checks compare native scenario deltas, risk,
capital scaling, ordering and improvement without duplicating discounting.

Canonical helpers live under `tests/data_engine/testing` and dashboard
`unit/data-engine/testing`. Synthetic UUIDs, source references and one bank scope
link batches, lineage, products, borrowers, positions and their snapshots. Amounts
use the canonical six-place storage precision and fractional rates ten places.
Validity properties run the public ingestion validator and API conversions;
identity, provenance and Decimal strings compare exactly. Canonical reordering
and amount scaling preserve record acceptance. No fixture provisions bank data
through a product route, and no generator queries production or customer data.

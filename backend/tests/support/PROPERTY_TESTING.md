# Feature-owned property inputs and assertions

The backend helpers live in `tests/<feature>/testing/{generators,assertions}.py`.
Dashboard helpers live in `unit/<feature>/testing/{generators,assertions}.ts`.
They belong to the feature test package so Hypothesis, fast-check and assertion
libraries stay out of production imports. Cross-feature mechanics belong in
`tests/support`; keep financial assumptions and tolerances with their owner.

## Find the domain helpers

Each linked package owns its generators and assertions. Backend validity properties
live in the package's `test_generators.py`; dashboard validity properties live in
the feature suites under `unit`. Filing, ICAAP and approval models also validate
generated initializer histories through public service commands.

| Domain                                 | Backend test package                   | Dashboard test package                                                           |
| -------------------------------------- | -------------------------------------- | -------------------------------------------------------------------------------- |
| Filing and reporting calendars         | [reporting](../reporting/testing/)     | [submissions](../../dashboard/unit/submissions/testing/)                         |
| ICAAP workflow and freeze              | [icaap](../icaap/testing/)             | Shared [submissions](../../dashboard/unit/submissions/testing/) authority inputs |
| Governed approvals and access requests | [identity](../identity/testing/)       | Shared [submissions](../../dashboard/unit/submissions/testing/) authority inputs |
| Liquidity                              | [liquidity](../liquidity/testing/)     | [liquidity](../../dashboard/unit/liquidity/testing/)                             |
| Capital                                | [capital](../capital/testing/)         | [capital](../../dashboard/unit/capital/testing/)                                 |
| Credit                                 | [credit](../credit/testing/)           | [credit](../../dashboard/unit/credit/testing/)                                   |
| FX                                     | [fx](../fx/testing/)                   | [fx](../../dashboard/unit/fx/testing/)                                           |
| FTP                                    | [ftp](../ftp/testing/)                 | [ftp](../../dashboard/unit/ftp/testing/)                                         |
| IRRBB                                  | [irrbb](../irrbb/testing/)             | [irrbb](../../dashboard/unit/irrbb/testing/)                                     |
| Canonical data                         | [data_engine](../data_engine/testing/) | [data-engine](../../dashboard/unit/data-engine/testing/)                         |

## Generator contract

Each generator cites the schema, engine or regulatory source in its docstring.
Synthetic realistic inputs are the default; deliberately include valid edges and
name invalid/adversarial inputs separately. Generator self-tests execute public
validators or engines. Stateful initializer strategies are also exercised through
real commands: never install a generated status directly into a database row.
Use the source's numeric kinds, units, precision and rounding. Bias examples toward
thresholds and their immediate neighbours without inventing regulatory values;
label internal policy, pending calibration and synthetic market bands explicitly.
Never query production or customer data to populate a strategy.

Filing calendar inputs use registry cadence and deadline metadata in Accra time,
including leap-year period ends and observations one second either side of a
cutoff. Governed ICAAP deadlines remain absent until a parameter is supplied;
midnight observations for date-only deadlines do not claim a regulatory cutoff.
Review histories use distinct synthetic officers and fresh decisions after returns.
The service machines retain unauthorized actors, stale digests and illegal actions.

## Assertions and tolerances

Assertion modules name the business invariant and report the relevant evidence.
They compare outputs or relate runs; they must not implement the engine formula.
Filing, ICAAP freeze and governed approvals compare evidence exactly: their
`TOLERANCE` policies are zero. Each financial domain owns one policy that names
the bounds for its quantities, units and rounding sources. The shared
[`TolerancePolicy`](assertions.py) is an absolute Decimal comparison primitive;
structured domain policies group money, fractions, percentage points and display
quanta without treating them as interchangeable. Choose bounds from actual engine
rounding, including error propagated through transformations and cancellation,
never an arbitrary epsilon. Dashboard filing uses an equivalent exact
`filingTolerance` policy.

Metamorphic checks here cover decision-order independence, officer renaming and
selected-bank authority under capability reordering or sibling changes. Financial
scaling, zero-exposure and currency round trips belong with the financial domains.
Do not carry monetary tolerance into status, identity or cryptographic comparisons.

## Reuse or extend a helper

1. Import the owning generator and assertion module into the feature's property
   test; keep property-test dependencies out of production packages.
2. Derive any new input relationship or precision from its cited public contract.
   Prefer constructive generation over filtering arbitrary values until they pass.
3. Add a validity property that executes the public validator, engine or API
   conversion. For a workflow, drive the real commands from its initial state.
   Preserve refusal cases separately so realistic defaults cannot hide them.
4. Use the owning assertion to check an independent invariant or compare transformed
   runs. Apply scaling, ordering, zero exposure or currency round trips only when
   the domain contract makes that relationship meaningful.
5. Exercise the assertion's failure message with deliberately corrupted evidence;
   a validity check must reject a broken relationship, not merely accept fixtures.
6. Keep the existing example budget and replay information. If a property fails,
   minimize the example and identify whether the engine, generator or assertion
   is wrong. Keep a regression and report a production defect explicitly rather
   than changing a tolerance to conceal it.

See the [backend capital validity properties](../capital/testing/test_generators.py)
and [dashboard financial validity properties](../../dashboard/unit/financial-generators.spec.ts)
for executable examples. The source notes below explain each domain's assumptions.

## Bounded validation

Keep the existing CI budgets and regression coverage documented in the
[backend workflow guide](../../docs/state_machine_testing.md#bounded-reproducible-execution)
and [dashboard testing guide](../../dashboard/README.md#unit-test-collection).
The [shared testing standard](../../../CODEBASE_CONVENTIONS.md#behavior-properties-and-state-machines-backend-and-dashboard)
governs both surfaces.

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
capital scaling, ordering and improvement without duplicating discounting. Ratio
scaling propagates each run's half-quantum monetary rounding per material currency
through its Tier 1 denominator, then adds one percentage quantum; cancellation
can make a fixed percentage-only tolerance too small. Dashboard mandatory and
outlier-set measure membership is derived from the corresponding scenario flags
and checked exactly before and after normalization; stored figures remain exact.

Canonical helpers live under `tests/data_engine/testing` and dashboard
`unit/data-engine/testing`. Synthetic UUIDs, source references and one bank scope
link batches, lineage, products, borrowers, positions and their snapshots. Amounts
use the canonical six-place storage precision and fractional rates ten places.
Validity properties run the public ingestion validator and API conversions;
identity, provenance and Decimal strings compare exactly. Canonical reordering
and amount scaling preserve record acceptance. No fixture provisions bank data
through a product route, and no generator queries production or customer data.

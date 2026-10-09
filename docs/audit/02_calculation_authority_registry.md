# 02. Calculation Authority Registry

**Module:** `backend/app/domain/authority/registry.py` (1,785 lines)
**Tests:** `backend/tests/domain/authority/test_registry.py`, `backend/tests/equivalence/test_declared_divergences.py`
**Prepared:** 2026-08-22

The forensic architecture audit's central finding was that a metric could be produced by more
than one code path with no declaration of which one is authoritative. The registry is the
answer to that finding (`ARCH-1`): for every metric the platform can produce, it names the
regime, the methodology, the engine, the policy resolver, the parameter set, the
authoritative run type, the reporting mappings, and whether the result may be filed.

---

## 1. Registry census

Generate the current census from the executable registry; copied counts are not
an authority for the live filing designations or verification backlog:

```sh
cd backend && uv run python -c "
from collections import Counter
from app.domain.authority.registry import REGISTRY
print('total authorities:', len(REGISTRY.all()))
print('counts_by_family:', dict(REGISTRY.counts_by_family()))
print('advisory_designation:', dict(Counter(e.advisory_designation.value for e in REGISTRY.all())))
print('requiring_external_verification:', len(REGISTRY.requiring_external_verification()))
for metric, entries in REGISTRY.multi_authority_metrics().items():
    print(metric, [(e.methodology_id, e.advisory_designation.value) for e in entries])
print('unresolved_divergences:', [(e.metric_id, e.methodology_id) for e in REGISTRY.unresolved_divergences()])"
```

## 2. Uniqueness is structural

The uniqueness key is `AuthorityKey(metric_id, regime, methodology_id)`. `register()` raises
`DuplicateAuthorityError` on a collision (`registry.py:434`), and
`test_every_metric_regime_pair_has_at_most_one_primary_authority` pins that at most one
authority per (metric, regime) pair is `is_primary`.

Each `MetricAuthority` record carries 26 fields (`registry.py`, dataclass fields):

| Field group        | Fields                                                                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Identity           | `metric_id`, `metric_family`, `institution_class`, `jurisdiction`, `regulator`, `regime`, `methodology_id`                                |
| Governance         | `return_family`, `effective_from`, `effective_to`, `advisory_designation`, `authority_reference`, `is_primary`, `audit_findings`, `notes` |
| Computation        | `canonical_inputs`, `policy_resolver`, `calculation_engine`, `calculation_version`, `parameter_set`, `authoritative_run_type`             |
| Divergence control | `reporting_mappings`, `expected_tolerance`, `approved_alternate_methodologies`, `divergence`, `forbidden_alternative_sources`             |

Worked record — the primary authority for bank CAR:

```
metric_id='car_pct'  family=capital  class=bank  jurisdiction='GH'  regulator='BOG'
regime=crd  methodology_id='crd_basel_capital_run'
policy_resolver='app.services.params:get_active_params'
calculation_engine='app.domain.capital.engine:compute_capital_ratios'
calculation_version='regulatory-capital-v1.0.0'
parameter_set=('ParamRiskWeight', 'ParamCapitalThreshold')
authoritative_run_type='capital'
reporting_mappings=('CAR-RWA', 'BSD5A', 'BSD5B')
expected_tolerance=Decimal('0')
advisory_designation=FILED
forbidden_alternative_sources=('app.services.calculations:calculate_forecast',
  'app.services.liquidity:calculate_metrics', 'app.services.capital:_indicator',
  'app.models.calculation:CalculationRun', 'app.models.calculation:CalculationForecastPeriod',
  'app.models.capital:CapitalProjection', 'dashboard client-side arithmetic')
```

The `forbidden_alternative_sources` list is the machine-readable form of the case-plane
boundary described in §01 §2.

---

## 3. Designations, and what each permits

The executable [`AdvisoryDesignation`](../../backend/app/domain/authority/registry.py)
enum owns each designation's meaning, including `basel_reference`; the census
command in §1 derives their counts. BI's projection is owned by
[`app/domain/bi/authority.py`](../../backend/app/domain/bi/authority.py).

`test_unresolved_divergences_are_never_designated_filed` is the guard: an authority whose
`divergence.resolution_status` is not `accepted_by_authority` can never carry `FILED`.

---

## 4. The six metrics with more than one authority

| Metric                         | Methodologies                                                                                                                                                                                                 | Status                                                   |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `car_pct`                      | `crd_basel_capital_run` (primary, filed) · `bog_bsd5a_form_ratio` (filed, accepted divergence) · `act930_s29_nof_rwa` (SDI primary, monitoring) · `bank_forecast_projection_path` (advisory only, unresolved) | see §5, §6                                               |
| `total_rwa_ghs`                | `crd_basel_capital_run` (filed) · `act930_s29_nof_rwa` (monitoring)                                                                                                                                           | Different legal regimes                                  |
| `lcr_pct`                      | Current methods and filing designations: executable `REGISTRY` (§1 census)                                                                                                                                    | see §5                                                   |
| `nsfr_pct`                     | Current methods and filing designations: executable `REGISTRY` (§1 census)                                                                                                                                    | see §6                                                   |
| `npl_ratio`                    | `bog_five_grade_classification` (bank, filed) · `nbfi_four_grade_classification` (SDI, monitoring)                                                                                                            | SDI methodology rests on a repealed instrument — see §15 |
| `total_provision_required_ghs` | as `npl_ratio`                                                                                                                                                                                                | as above                                                 |

---

## 5. Accepted divergences — declared, not equated

Two alternate methodologies are declared legitimate and their difference is stated rather
than engineered away.

### 5.1 `car_pct` via `bog_bsd5a_form_ratio`

> _"Same NOP and gross income, different add-on rules: BoG takes 50% of NOP and 100% of the
> 3-year average gross income; the engine takes the FX charge × RWA multiplier and the BIA
> charge × RWA multiplier. Credit is weighted by BoG's printed classes rather than the
> standardised weights."_

The inequality is proved, not assumed:
`tests/services/bog_forms/test_bsd5.py:390` asserts `not _close(...)`
_"by construction, not by accident"_, and the reporting-equivalence suite carries
`test_the_bsd5a_car_inequality_is_left_alone`
(`tests/services/test_reporting_equivalence.py`) so the equivalence gate can never be
mistaken for a licence to equate declared alternates.

### 5.2 `lcr_pct` via `lmtd_table11_capped` (`CF-1`)

The registry text is explicit that **both** methodologies cap inflows and the divergence is
in _how_: Table 11 applies a hard-coded 75% cap (`le_generation._LCR_INFLOW_CAP`)
**per currency column**; the `LCR-NSFR` return applies one **aggregate** cap at the
governed, effective-dated `lcr_inflow_cap_pct` threshold, required by
`regulatory_liquidity._REQUIRED_THRESHOLDS` and applied unconditionally in
`app/domain/liquidity/engine.py::compute_lcr`.

> **Withdrawn claim, recorded.** An earlier version of this registry entry — and the
> forensic audit that prompted it — stated that the `LCR-NSFR` return applies _no_ inflow
> cap. **That was false.** The registry text now says so in terms
> (_"An earlier version of this entry said the LCR-NSFR return applies no cap. That was
> FALSE - do not act on it."_), and two tests pin the corrected position:
> `test_the_two_lcr_methodologies_diverge_in_mechanics_not_in_having_a_cap` and
> `test_the_bsd3_engine_caps_inflows_in_aggregate`
> (`tests/equivalence/test_declared_divergences.py`).

A second, related correction from the source audits: **there is no BoG return code `BSD3`
for liquidity.** `BSD3A`/`BSD3B` are the Large Exposures returns; migration `202608150013`
recoded the liquidity and capital reconstructions to `LCR-NSFR` and `CAR-RWA`. Only their
`template_id` strings still read `bog-bsd3-liquidity-v1`, which is what misled the audit.
There is likewise no return code `LMTD` — it is `LMT`.

---

## 6. The unresolved divergence, and its containment

`car_pct`, `lcr_pct` and `nsfr_pct` computed via `bank_forecast_projection_path` are
registered `divergence.resolution_status = unresolved_audit_finding` and designated
`ADVISORY_ONLY`. **They therefore cannot be filed while divergent.** The `car_pct` entry
carries the two source-audit findings verbatim in `audit_findings`.

The underlying numeric divergence has since been diagnosed and closed, and the
diagnosis matters because the audits named only one of three causes:

| Cause                                       | Named by the audits? | Effect                                                                           |
| ------------------------------------------- | -------------------- | -------------------------------------------------------------------------------- |
| Forecast snapshot excluded `ecl_exposure`   | Yes                  | Dormant on the live book — the facts exist but no `param_ecl_assumption` rows do |
| Forecast snapshot excluded `crm_collateral` | No                   | Moves the CAR denominator; dormant on the live book                              |
| Operational-RWA base mismatch               | No                   | The dominant live contributor                                                    |

The resolution did **not** force two methodologies to agree: both sides call the same
function, `compute_capital_ratios`. One methodology was being handed two different fact
sets. Proof suite: `tests/equivalence/` — 13 parity assertions in
`test_forecast_capital_parity.py` (including
`test_year_zero_capital_ratios_equal_the_capital_run` parametrised over ECL and CRM, and
`test_any_capital_group_the_forecast_drops_is_provably_inert`). Tolerances are
`Decimal(0)` throughout — a non-zero tolerance would hide a methodology difference.

**The registry designation was deliberately left `ADVISORY_ONLY`.** Numeric equality under
the tested baseline is not the same as an enforced identity, and the registry records the
weaker, true statement.

---

## 7. The registry cannot silently gain an unclassified claim

`tests/equivalence/test_declared_divergences.py` classifies every reporting claim the
registry makes and **fails if a new claim appears unclassified**
(`test_every_reporting_claim_the_registry_makes_is_classified`). Three further guards:

- `test_declared_divergences_are_exactly_the_registry_entries_without_a_tolerance`
- `test_no_claim_the_registry_forbids_equating_is_proved_equal_here`
- `test_every_alternate_methodology_documents_its_divergence`

That is how a future divergence gets caught rather than discovered by an examiner.

---

## 8. Authorities requiring external regulatory verification

`requires_external_verification()` is a **substring** test over `authority_reference`,
`policy_resolver` and `calculation_version` (`registry.py:356-370`) — deliberately, because
several citations are partially established. A metric is flagged if any governance field
still carries the sentinel.

The current family split and each entry's unresolved basis are generated from
`REGISTRY.requiring_external_verification()` below. This includes Basel references
whose BoG instrument or calibration is unpublished; the register's notes own the
specific gap rather than a copied family/count table.

Full enumeration (metric · regime · class · designation) is reproducible with:

```sh
cd backend && uv run python -c "
from app.domain.authority.registry import REGISTRY
for e in sorted(REGISTRY.requiring_external_verification(),
                key=lambda x:(x.metric_family.value, x.metric_id)):
    print(e.metric_family.value, e.metric_id, e.regime.value,
          e.institution_class.value, e.advisory_designation.value)"
```

What each one needs is itemised in §15 §2. The corresponding parameter-side gap — values
whose `confirmation_status` cannot honestly be `confirmed` — is §04 §5 and §15 §3.

---

## 9. Reporting authority is stamped, not assumed (`ARCH-4`)

Every generated package is stamped in one place
(`app/services/regulatory_reporting/provenance.py::_stamp_provenance`), so a new generator
cannot ship without a stated authority. `ReportAuthority` distinguishes
`template_formula` (the BoG workbook's own formula is the authority) from `engine_run`
(a sealed `RegulatoryRun` is the authority) and `guide_instruction`.

`authority_for_resolver` raises if a resolver declares no authority
(`provenance.py:311-327`), pinned by
`test_every_resolver_declares_an_authority` — so a resolver cannot quietly claim template
authority for something nobody reviewed. `declared_methodologies` are resolved against the
registry and degrade to `EXTERNAL_REGULATORY_VERIFICATION_REQUIRED` when unregistered.

### A false statement was being printed on every BoG-form artifact

Exported artifacts previously carried the blanket line _"Every figure traces to the source
calculation runs below"_. That is **false for every template-authoritative form**, where the
workbook's own formulas are the authority and no calculation run stands behind the figure at
all. It is now stamped honestly in three places: `authority == "template_formula"`, a
non-null `source_runs_rationale` (_"template-authoritative: … no calculation run stands
behind it"_), and the artifact text itself.

---

## 10. Open registry defects, not closed by this programme

| ID              | Item                                                                                                                                                                                   |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NEW-33`        | The registry maps `total_rwa_ghs → BSD5A!E69` at tolerance 0, but E69 is BoG's _adjusted asset base_ and `E70 = E25/E69` is pinned **not** equal to the engine CAR. Both cannot hold.  |
| `NEW-34`        | The registry claims BSD13 / DBK-DAILY carry `single_ccy_max_pct`, `stressed_var_ghs`, `var_99_1d_ghs`; **no BSD13 line binds them.**                                                   |
| `NEW-31`        | `ParamCapitalThreshold` and `ParamRiskWeight` carry no `institution_class` axis, so in a mixed organisation an SDI binds against the bank's board `car_min`. Needs a migration.        |
| `CF-5` residual | Forecast is blocked for SDI tenants at 5 entry points, driven by the registry rather than a hand-written class check — but the SDI _stress_ methodology itself is not built (see §15). |

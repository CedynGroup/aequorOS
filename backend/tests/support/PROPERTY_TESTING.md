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

Keep the existing CI budgets: backend profile 25 examples, database machines
12 examples and 20 steps; dashboard pure properties 100 runs and filing models
20 runs. Preserve minimized failures as regressions and report real defects.
The full testing standard is in `CODEBASE_CONVENTIONS.md`; the remaining domain
migrations and standard-wide guidance follow in the sequential domain changes.

The ICAAP narrative migration exposed two gaps in the previous test model:
`draft → blank paragraph → commit` correctly returns `422 section_empty`, and
`draft → commit paragraph → save/commit the same paragraph` correctly returns
`409 no_changes`. The model now tracks the last committed text and asserts
refusal evidence. `test_empty_and_repeated_edits_preserve_committed_history`
retains both minimal examples. These are model corrections, not product defects;
production section-commit behavior is unchanged.

# The AequorOS analytics feed — a guide for your BI team

**Audience:** the bank's BI, MIS or data team, and the IT function that will hold
the credential. **Status:** as-built for `docs/bi.md` §Phase 4 "Power BI Stage B
feed", built 2026-09-27. **Companion documents:** `docs/API_INTEGRATION.md` §1
(integration keys) and §8 (the feed's request and response contract).

This document answers four questions a BI team asks before it builds anything on
a new source: what am I allowed to connect to, what exactly will I receive, how
do I keep my copy correct over time, and what is my institution signing off on.

---

## 1. Why there is no database login

The first thing most BI teams ask for is a read-only Postgres user and a direct
connection from the report server. **AequorOS will not issue one, and the reason
is a property of the database rather than a policy preference.**

Every tenant-scoped table in the platform is protected by PostgreSQL row-level
security, and the policy keys on a *session* setting — `app.organization_id` —
that the application sets at the start of each transaction. That design is what
makes it impossible for one institution's request to read another's rows. It also
means the setting is a property of the connection, not of the credential:

- a direct reader that never sets it **sees nothing at all**, because the policies
  are `FORCE`d and match no row; and
- a direct reader that is granted the ability to set it — or that is given a role
  which bypasses RLS so it can "just see its own data" — **is a credential that
  can read every tenant on the platform**, because the value is a plain string
  that the session itself chooses.

There is no third option. Worse, a report server or gateway connects through a
connection *pool*: connections are reused across refreshes, so even a
well-behaved direct reader has no stable place to put the tenant identity. A
pooled connection that inherits the previous refresh's setting is the failure
mode, and it fails silently — with numbers, not an error.

The partitioned analytics tables add a second edge: PostgreSQL does not inherit
row-level security onto partitions. AequorOS creates every monthly and yearly
child through a migration-owned function that re-applies `ENABLE` + `FORCE` RLS
and the tenant policy to each one. A direct reader querying a named child rather
than the parent is exactly the query that would slip past a partial
implementation of that.

So the feed exists precisely to be the thing a report server connects to. It is
authenticated by a credential that **is** the institution, carries no session
setting for anyone to get wrong, and writes a record of every pull.

---

## 2. What you connect to

One HTTP endpoint, one dataset per request:

```
GET https://api.aequoros.com/api/v1/banks/{BK-XXXXXXXX}/bi/feeds/{dataset}
    ?format=ndjson|csv
    &cursor=<the token the previous pull returned>
Authorization: Bearer aeq_live_…
```

- `format` defaults to `ndjson` (one JSON object per line). `csv` returns a
  header row of column names followed by data rows — no preamble, no metadata
  block, so `Csv.Document` and equivalents parse it without a skip count.
- Column names are AequorOS **catalogue member ids** (`loans.balance_rc`,
  `branch.code`). They are stable wire keys: renaming one is a versioned change to
  the catalogue, announced through `X-Bi-Feed-Catalogue-Version`.
- Amounts are exact decimals, in the institution's own reporting currency (stated
  once, in `X-Bi-Feed-Unit`, never in a column name).
- **A missing figure is empty, never zero.** An empty CSV cell and a JSON `null`
  mean "there is nothing to measure here". They do not mean nought. Do not
  coalesce them to 0 in your model; a zero in a provision column is a statement
  about the bank.

### The curated datasets

You cannot name a table, a column, or a filter. You name one of these:

| Dataset | One row is | Columns |
|---|---|---|
| `loan_book` | one reporting date × branch × product family × economic sector × impairment stage | `time.date`, `branch.code`, `product.family`, `loan.sector`, `loan.ifrs9_stage`, `loans.balance_rc`, `loans.npl_exposure_rc`, `loans.par_90_exposure_rc`, `loans.provision_required_rc`, `loans.provision_held_rc`, `loans.collateral_rc`, `loans.count` |
| `deposit_book` | one reporting date × branch × product family × account type × maturity bucket | `time.date`, `branch.code`, `product.family`, `position.deposit_account_type`, `position.maturity_bucket`, `deposits.balance_rc`, `deposits.demand_balance_rc`, `deposits.count` |
| `regulatory_metrics` | one reporting date, for the whole institution | `time.date`, then the filed (`.official`) and continuously re-derived (`.live`) capital, liquidity, interest-rate and foreign-exchange ratios |

The exact column list for a dataset is also returned on every pull, in
`X-Bi-Feed-Columns`. Validate against that header rather than hard-coding a
schema and hoping.

Three things the registry deliberately does **not** contain:

- **No record-level dataset.** There is no obligor list, no position-level
  extract, no employer or counterparty name. The feed credential carries read
  authority over *aggregated* figures only, so a dataset naming a legal person or
  a single account could not be served even if it were registered. If your board
  pack genuinely needs named exposures, take a governed export from the
  application, where a named human's authority and signature are on it.
- **No flow dataset yet** (disbursements, repayments, write-offs by period).
  These are keyed by event date rather than by reporting date, which the cursor
  below does not yet model. It is a known gap, not an oversight.
- **No general-ledger dataset yet.** The GL mart exists, but no catalogue measures
  are defined over it, and the feed serves only what the catalogue governs.

---

## 3. Keeping your copy correct: the cursor

This is the part that decides whether your model agrees with the bank's filed
numbers a year from now. Read it before you write the refresh.

**A bank's book is restated.** A correction to March arrives in September; a
withdrawal is reversed; a supervisory parameter changes and a whole month is
re-derived. AequorOS rebuilds the affected reporting date from scratch when that
happens — the figures for an old date change while the date does not.

So **the cursor is not a business date.** It is an opaque position in *build*
time. Concretely:

1. You pull with no `cursor`. You receive every reporting date the platform has
   built, oldest build first, plus `X-Bi-Feed-Next-Cursor`.
2. You store that token and send it next time. You receive only what has been
   **built or rebuilt** since — which includes a restatement of a date you already
   have.
3. `X-Bi-Feed-Reporting-Dates` lists exactly which dates the payload covers.

**Your one obligation, and the whole contract:**

> **Replace by reporting date.** For every date in `X-Bi-Feed-Reporting-Dates`,
> delete your own rows for that date, then insert the rows in the payload.

Append-only loading is wrong here and will silently double-count a restated
month. Because replacement is idempotent, re-delivery is harmless — and the feed
deliberately errs towards re-sending:

- **`X-Bi-Feed-Next-Cursor: none`** means *do not advance your cursor*: send the
  same one again next time. It happens when everything served sits at or after a
  build that was still running when you pulled. Serving you a date twice costs a
  refresh; skipping one costs a year of wrong numbers.
- **`X-Bi-Feed-More-Available: true`** means there are older, unserved dates: pull
  again immediately with the new cursor. A first synchronisation of a bank with
  years of daily history is several pulls, by design — one response holding all of
  it would fail whole rather than fail partially.
- **A malformed cursor is a `422`, never a silent full resynchronisation.** If you
  genuinely want to reload from scratch, omit the parameter.

**Only complete reporting dates are served.** A date whose analytics build did
not finish successfully is absent from the feed — never present with partial or
zero figures. So "a date is missing" means "not yet built", and it will appear
when it is.

### Reading the other response headers

| Header | What it tells you |
|---|---|
| `X-Bi-Feed-Data-Scope` | The slice of the institution this credential covers: the whole institution, or the named branches. **If this changes, your dataset has changed shape.** Alert on it. |
| `X-Bi-Feed-Trust` | The reconciliation verdict over the dates served: `green`, `amber`, `red`, or `grey` for "not assessed". `grey` is never a pass. Surface it on any page built from the feed. |
| `X-Bi-Feed-Build` | The fingerprint of the analytics build the rows came from. Two pulls with the same fingerprint read the same book. |
| `X-Bi-Feed-Catalogue-Version` | Which definitions were in force. A change here can change what a column means. |
| `X-Bi-Feed-Reporting-Date-Count` | How many dates the payload covers, for a cheap sanity check against your own load. |

---

## 4. The credential

The feed is authenticated by an **integration key issued for one purpose and one
institution**. Your account administrator issues it in the application; it is
shown exactly once and only its hash is stored, so if it is lost it is reissued,
never recovered.

- A key is issued as either a **data push** key or an **analytics feed** key, and
  the two are strictly disjoint authorities. A push key is refused by the feed; a
  feed key is refused by every push route. Neither is a configuration you can get
  wrong — they carry different permissions and cannot substitute for one another.
- A feed key names **one exact institution**. Presented against a sibling
  institution of the same group it answers `404`, exactly as another
  organisation's institution would: a credential cannot map your estate.
- A feed key may be **narrowed to branches or regions**. It then serves only
  those branches, on every dataset, and the institution-wide ratios in
  `regulatory_metrics` are refused outright — a capital ratio computed over three
  branches is a wrong number with a right-looking name.
- **Revocation is immediate and complete**: the credential, its authority and its
  machine identity are ended in one transaction. Revoke on any suspicion, and
  rotate on your own schedule.
- Treat the key as you would a core-banking credential: store it in the report
  server's own secret store or gateway credential store, never in a workspace
  parameter, a shared query, a `.pbix` file, or an email.

**Every pull is recorded** — which credential, which dataset, which cursor, how
many rows, and whether it was allowed or refused. Ask for that record whenever
you want it; it is the evidence you will want during an audit, and it is the
reason the feed exists in preference to a database login.

---

## 5. Where the data is allowed to go — the CISD caveat

**What CISD is, in this repository's context.** The Bank of Ghana's **Cyber and
Information Security Directive**; the 2025 exposure draft is cited in
`docs/research/bog_orass_submission_channels.md` (comments to
`information.security@bog.gov.gh`, exposure draft published September 2025). This
platform's product documentation (`docs/bi.md`) records the operative expectation
as: **critical data is expected to stay in Ghana**, with the definition of
"critical data" to be confirmed by counsel. Nothing in this document is legal
advice, and AequorOS does not determine which of your data is critical — your
institution does.

**Why it matters to a BI project at all.** Microsoft Power BI's nearest data
region is South Africa. A Power BI *Service* (cloud) dataset therefore holds a
copy of whatever it imports, outside Ghana, on infrastructure your institution
does not control. That is true of any cloud BI service; Power BI is named because
it is the tool this feed most often replaces or feeds.

### What is recommended

1. **Preferred — keep the data in country.** Use the AequorOS analytics
   surfaces (dashboards, Explore, governed exports, scheduled report packs) and
   pull nothing out. Then no copy of the bank's data leaves the deployment, and
   the residency question does not arise.
2. **Recommended, if you want your own BI tool — Power BI Report Server**, or an
   equivalent on-premises deployment, hosted on infrastructure inside Ghana. The
   feed is pulled into a model that never leaves the country. This is the
   configuration this document is written for.
3. **Acceptable with sign-off — a gateway with the model kept on-premises.** An
   on-premises data gateway lets a cloud workspace query an in-country source.
   Note precisely what this does and does not achieve: a *DirectQuery* model
   leaves the rows in country and sends only query results; an *Import* model
   through a gateway **copies the rows into the cloud region**, and is
   configuration 4 wearing the clothes of configuration 3. Confirm which one your
   dataset uses before you sign anything.
4. **Requires an explicit institutional decision — a cloud import model.** The
   bank's data is copied to a region outside Ghana. AequorOS does not prevent
   this; the credential is yours. But it is your institution's decision, not your
   BI team's, and it should be recorded as one.

### The residual risk, stated plainly

- **Once data leaves through this feed, the platform's controls stop.** Inside
  AequorOS, every figure carries a data-scope filter the reader cannot remove, a
  reconciliation verdict, and a logged authorization decision. A copy in a report
  model carries none of that. Whoever can open the report can see the whole model,
  whatever their authority in the bank; a branch-scoped feed key limits what is
  *pulled*, not who reads the report afterwards.
- **The feed's own record ends at the pull.** AequorOS can tell you that a
  credential took `loan_book` for eleven reporting dates at 03:00. It cannot tell
  you where those rows went next.
- **A copy can go stale and look current.** A report model that stops refreshing
  still renders. `X-Bi-Feed-Build` and `X-Bi-Feed-Reporting-Dates` are how you
  make staleness visible; nothing else will.
- **Aggregated is not anonymous.** These datasets name no person, but a branch ×
  product × sector cell can be small, and a small cell can be one obligor. Treat
  the feed's output as bank-confidential material, not as published statistics.

### What the bank is signing off on

Before a production feed key is issued, the institution should record a decision
that names, in its own document:

1. **the configuration** — which of options 1–4 above, and for a gateway model,
   Import or DirectQuery;
2. **the classification** — whether the datasets pulled constitute "critical
   data" under the CISD as the bank's counsel reads it, and if a copy will sit
   outside Ghana, the basis on which that is accepted;
3. **where the copy lives** — the hosting location and operator of the report
   server, gateway and any workspace involved;
4. **who may read the resulting reports**, and how that compares with who holds
   the equivalent authority inside AequorOS — this is the control the feed cannot
   enforce for you;
5. **the credential custodian**, the secret store it lives in, and the rotation
   and revocation schedule;
6. **the accountable owner** — a named officer, because that is what makes the
   four points above reviewable.

AequorOS's part of the sign-off is this document plus the pull record; the
classification and the hosting decision are the bank's, and legal sign-off on
CISD is expected before a production tenant enables the feed.

---

## 6. A worked Power BI Report Server setup

A minimal Power Query pattern. The key comes from the gateway or report-server
credential store — **not** from a parameter saved in the file.

```m
let
    Base     = "https://api.aequoros.com/api/v1/banks/BK-XXXXXXXX/bi/feeds/",
    Dataset  = "loan_book",
    Cursor   = CursorFromYourControlTable,          // "" on a first load
    Response = Web.Contents(
        Base & Dataset,
        [
            Query   = if Cursor = "" then [format="csv"] else [format="csv", cursor=Cursor],
            Headers = [#"Authorization" = "Bearer " & KeyFromSecretStore]
        ]
    ),
    Table    = Csv.Document(Response, [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Table, [PromoteAllScalars=true])
in
    Promoted
```

Then, in the load step:

1. read `X-Bi-Feed-Next-Cursor` and `X-Bi-Feed-Reporting-Dates` from the response
   metadata (`Value.Metadata(Response)[Headers]`);
2. delete your rows for every date in `X-Bi-Feed-Reporting-Dates`;
3. insert the payload;
4. store the next cursor **only if** it is not `none`;
5. if `X-Bi-Feed-More-Available` is `true`, repeat immediately.

Steps 2 and 4 are the whole correctness argument. A loader that appends, or that
stores `none` as a cursor, will be wrong and will not say so.

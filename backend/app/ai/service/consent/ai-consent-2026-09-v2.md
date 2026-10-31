# AI assistance: what your organisation is agreeing to

Version `ai-consent-2026-09-v2`

Your Organisation Owner must read and accept these terms before AI assistance can
be switched on for your organisation. Switching it off again never requires
consent and always takes effect immediately.

This version covers **two** features. Version `ai-consent-2026-09-v1` covered AI
drafting only; asking a question in your own words is new in this version, and it
sends something the first version did not describe — **your staff member's own
words**. That difference is why this text is a new version rather than an edit,
and why accepting it is a fresh decision rather than a formality.

## 1. AI drafting

### What the feature does

AI drafting writes a **first draft** of a narrative section of a regulatory
document. A member of your staff reads it, edits it, and chooses whether to
insert it. Nothing an AI model produces is ever added to a document, committed,
frozen, signed or filed without that person's explicit action.

### What leaves the platform

When one of your staff requests a draft, the platform sends a **fact sheet** to an
AI sub-processor — see "Sub-processors" below, which is not limited to one vendor.
The fact sheet is pseudonymised and frozen at the moment of the request. It
contains:

- the **keys and roles** of the parties involved — "the reporting institution",
  "the prudential regulator" — never their names;
- **ratios and indicators** from the figure blocks the section has linked, such
  as a capital ratio;
- **precomputed descriptors**: whether a figure meets, sits at or breaches its
  regulatory limit; whether it is higher, lower or unchanged against the prior
  year; whether it sits inside risk appetite;
- the **section's own checklist** from the published regulatory framework, which
  is public text and contains nothing about your organisation.

### What never leaves the platform

- **People.** No name, title, email address or identifier of any individual —
  staff, officer, director, shareholder or customer — is ever sent.
- **The names of your organisation, your institution or any former name.**
- **Monetary amounts and dates**, which are replaced by placeholders and
  descriptors. Absolute balance-sheet size identifies an institution in a small
  market even when its name is removed.
- **Your country, your regulator, your central bank and your currency**, which
  reach the model only as placeholder keys.
- **Customers, exposures, accounts, transactions, documents and attachments.**

If your organisation selects **descriptors only**, no figure values are sent at
all — only the comparisons above. The model still places every figure, and the
platform fills in the verified value afterwards.

### How figures reach the finished text

The model is not permitted to write a number. It marks where a figure belongs,
and the platform substitutes the verified value from your own linked figure
block. A draft that writes a number anyway is rejected by an automated check and
is never shown to your staff — you see only a status saying it did not pass.

## 2. Asking a question in your own words

### What the feature does

A member of your staff types a question about your own reported figures — "gross
loans by branch for August" — and the platform proposes a **query**: the named
figures, the grouping, the filters and the date it would use. Your staff member
sees that proposal in plain language and chooses whether to run it.

**The model never sees your figures and never answers the question.** It only
turns words into a proposal. The proposal is then run by the platform's ordinary
reporting engine, under that person's existing access rights, against your own
data — the same path, and the same authority checks, as if they had built the
query by hand. A question proposes; only your staff member's confirmation
executes.

### What leaves the platform

Two things, and nothing else:

- **The question as your staff member typed it**, after the screening described
  below.
- **A catalogue of the figures and groupings that person is allowed to use** —
  their names and descriptions, such as "gross loans" or "branch". This is a
  list of what your institution *can report on*. It contains no figure, no
  balance, no customer, no branch code and no date from your book.

### What never leaves the platform

- **Any figure, balance, ratio or amount from your data.**
- **Any customer, exposure, account, transaction or document.**
- **Any part of the answer.** The result of the query is computed and displayed
  entirely inside the platform.
- **Figures that person is not entitled to see.** The catalogue sent is filtered
  to their access rights first, so a reader restricted to two branches cannot
  have the rest of your institution described to a model on their behalf.

### The screening, and what it can and cannot do

Before a question is sent, the platform removes it from the request if it
contains a name it knows: your organisation's name, your institution's name, any
former name, the names of related parties in your registers — individuals and
legal entities alike — the names of your own users, and your country, regulator
and central bank. A question that contains one of these is **refused, not
redacted**: your staff member is told to rephrase it.

One more limit, stated because it is not obvious: a name is only used as a
screening term when it is long enough to be unambiguous. A very short single-word
name — an abbreviation of two or three characters, say — is not screened for,
because matching it would refuse ordinary sentences that merely contain those
letters.

**And what the screening cannot do at all is know a name it has never been told.** If a
member of your staff types a customer's name, an account number, a national
identity number or a specific amount into the question box, the platform has no
register to match it against, and those words are part of the question that is
sent. Your staff should ask about **categories and figures** — "branches",
"loans over 90 days past due", "the corporate portfolio" — and never about a
named customer or a specific account.

This limitation is stated here rather than buried because it is the one way this
feature can send something the drafting feature never would. If your institution
is not willing to rely on staff training for that, do not switch this feature on:
AI drafting can be enabled without it.

### What is recorded, and where your question is kept

Every question records who asked it, what was proposed, and whether it was run.

**Your staff member's question is stored as they typed it**, on the platform's own
record of that request, so they can see what they asked and what it resolved to,
and so your administrators can audit it later. It is stored after the screening
described above, so a question the screening refused was never stored and never
sent. The
separate query log — the record of which figures were READ — stores a one-way
digest of the question rather than its text.

Be plain with your staff about that: a question is kept, in your own tenant, as
words. It is not deleted on a schedule today. It is not sent anywhere except as
described above.

A question refused before anything left the platform leaves a record showing that
nothing was sent: no figures were named to any model, and no request was queued.

## Sub-processors, residency and retention

**More than one AI provider may serve a request.** The platform is configured with
an ordered list of providers, and that list is not limited to one vendor: a request
may be served by Anthropic, and — depending on the configuration in force and on a
provider declining or failing — by another named provider instead.

Which providers may serve YOUR organisation is not open-ended. No request reaches
any model unless the exact combination of feature, model and provider has been
entered in the platform's approved-configuration register, and each entry names its
provider. **Ask your platform operator for the current approved list before you
accept these terms** — it is the authoritative answer to "where does our data go",
and this document deliberately does not restate it, because it changes without this
text changing.

Inference does not run in Africa; the platform records the geography that served
each request, and it is visible to your administrators. Retention and training
terms are governed by the agreement between the platform operator and each
provider; your platform operator will provide the current terms on request.

## Your controls

- Your Organisation Owner can switch either feature off at any time, for the
  whole organisation, without giving a reason. Requests already queued are
  cancelled rather than sent.
- Your Organisation Owner chooses **which** features are enabled — drafting and
  questions are separate switches — and whether descriptor-only mode applies to
  drafting.
- Every request records who asked for it, exactly what was sent, which model
  answered, and whether the result passed the automated checks. Every acceptance
  and rejection is recorded in your audit log and cannot be edited or deleted.
- The finished document states how many of its paragraphs were drafted with AI
  assistance and reviewed by your staff.

## Your own obligations

Your staff remain responsible for everything in a document your institution
files, and for what they type into a question. If your regulator requires
notification or approval before an outsourced or cloud-based service processes
your data, obtaining it is your institution's step, not the platform's.

## If these terms change

If this text is revised, its version changes and AI assistance switches off for
every organisation until an Organisation Owner reads and accepts the new
version.

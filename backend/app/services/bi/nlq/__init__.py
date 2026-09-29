"""Natural-language questions over the BI catalogue (docs/bi.md §Phase 5).

One sentence in the spec, and every clause of it is a boundary:

> the model emits a ``BiQuery`` restricted to the members the user can see, never
> SQL. It's shown to the user for confirmation and logged.

This package is the decision half. It writes NOTHING — no table, no queue row, no
log row — which is what keeps it inside the BI plane's rule (c): the writes live in
``app/jobs/bi_nlq.py`` (the queue row) and ``app/features/ask_bi.py`` (the query
log), exactly the split ``commentary``/``bi_commentary`` already makes.

Read in this order:

* :mod:`candidates` — which members the model may name, drawn only from the ones
  the caller can already see;
* :mod:`payload` — what is sent (catalogue metadata and the screened question, no
  figure and no date) and what is refused before it is sent;
* :mod:`prompt` — the byte-stable system block and the model request;
* :mod:`schema` — the only shape the model may answer in: a draft query in plain
  scalars, with no date field and no free text;
* :mod:`validate` — the refusal boundary. A draft becomes a ``BiQuery`` or it
  becomes a refusal; there is no third outcome;
* :mod:`reading` — the query read back to the reader, so confirming it means
  something.

Two properties that are easy to lose and are each pinned by a test:

**The model's output is a request, not an authority.** A proposed query is
executed through ``read_bi._authorize`` / ``_run`` — the same path a hand-built
query takes, including ``authorize_query`` — so there is no code path where "the
model chose it" stands in for a decision.

**A refusal names nothing the caller was not already entitled to know exists.**
``unrecognised_member`` is one public refusal for an id that does not exist, an id
the caller's grants hide, and an id the caller could see but which this question was
not offered. The ids themselves are recorded on the job for review and never
returned.
"""

from __future__ import annotations

from app.schemas.bi_nlq import QUESTION_MAX_CHARS
from app.services.bi.nlq.candidates import CANDIDATE_MEASURE_CAP, CandidateSet
from app.services.bi.nlq.candidates import select as select_candidates
from app.services.bi.nlq.payload import (
    SCREEN_MESSAGES,
    BuiltPayload,
    NlqScreenCode,
    QuestionWithheld,
)
from app.services.bi.nlq.payload import build as build_payload
from app.services.bi.nlq.payload import digest as payload_digest
from app.services.bi.nlq.payload import screen as screen_question
from app.services.bi.nlq.prompt import (
    PROMPT_VERSION,
    build_request,
    model_metadata,
    prompt_digest,
)
from app.services.bi.nlq.reading import Clause, Reading, measure_labels
from app.services.bi.nlq.reading import build as build_reading
from app.services.bi.nlq.schema import NlqDraft
from app.services.bi.nlq.validate import (
    REFUSAL_MESSAGES,
    UNANSWERABLE_MESSAGES,
    NlqRefusal,
    Translation,
    translate,
)

__all__ = [
    "CANDIDATE_MEASURE_CAP",
    "PROMPT_VERSION",
    "QUESTION_MAX_CHARS",
    "REFUSAL_MESSAGES",
    "SCREEN_MESSAGES",
    "UNANSWERABLE_MESSAGES",
    "BuiltPayload",
    "CandidateSet",
    "Clause",
    "NlqDraft",
    "NlqRefusal",
    "NlqScreenCode",
    "QuestionWithheld",
    "Reading",
    "Translation",
    "build_payload",
    "build_reading",
    "build_request",
    "measure_labels",
    "model_metadata",
    "payload_digest",
    "prompt_digest",
    "screen_question",
    "select_candidates",
    "translate",
]

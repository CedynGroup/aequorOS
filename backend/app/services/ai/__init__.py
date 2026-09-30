"""Shared AI foundation: gates, tenant consent, the model client, grounding.

Built once for every AI surface (ICAAP drafting today, BI commentary next), and
the ONLY place in the platform that sends tenant data to an external service.

Three things are load-bearing here and should be preserved by anything that
extends this package:

* ``gates.evaluate`` is called at BOTH enqueue and run. A job can sit in the
  queue across a kill-switch flip or a tenant switching the feature off; the
  second check is what stops that queued request from being the one call that
  still goes out.
* ``client`` is the only importer of ``anthropic``, and it imports it lazily
  inside the model class, so the API process, the core worker and the operator
  app never load the SDK or parse the key. Since D-053 the same holds per
  vendor: ``openai_model`` and ``google_model`` are the only importers of their
  transport, also lazily, and each holds its own credential class.
* ``complete_structured`` is THE entry point. It walks ``AI_PROVIDER_TIER``
  (``tiered``) and fails over on AVAILABILITY only — never on a refusal or a
  draft that fails grounding, which go to the feature's deterministic fallback.
  ``vendors`` is where that line is drawn, by failure code rather than by
  message.
* Nothing the model returns reaches a document without passing the grounding
  validator AND a human explicitly accepting it.
* Every result carries its vendor, model and tier position. For ICAAP that rides
  a FILED artifact, so it lands in the committed section version's provenance
  (``icaap/ai_provenance``), not only in a log line.
"""

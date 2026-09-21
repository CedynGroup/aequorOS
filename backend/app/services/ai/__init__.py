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
  app never load the SDK or parse the key.
* Nothing the model returns reaches a document without passing the grounding
  validator AND a human explicitly accepting it.
"""

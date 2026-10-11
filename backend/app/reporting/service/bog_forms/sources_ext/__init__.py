"""Per-form resolver extensions (collision-free contribution seam).

Each ``sources_ext/<form>.py`` registers additional ``@resolver`` functions a
form's line map needs (top-N depositor rankings, sector splits, maturity
buckets, P&L lines, NOP by currency, …). Modules are auto-imported here so a
line map can name them; keeping one module per form means parallel work on
different forms never touches the same file. Core resolvers stay in
``sources.py``.
"""

from __future__ import annotations

import importlib
import pkgutil

for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module.name}")

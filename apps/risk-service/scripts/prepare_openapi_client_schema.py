from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

NULLABLE_DATE_MODELS = {
    "CalculationRerunCreate",
    "CalculationRunCreate",
    "CalculationRunRead",
    "CalculationRunSummaryRead",
}


def prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    prepared = deepcopy(schema)
    components = prepared["components"]["schemas"]
    for model_name in NULLABLE_DATE_MODELS:
        components[model_name].pop("additionalProperties", None)
    return prepared


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare_openapi_client_schema.py INPUT OUTPUT")
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    schema = json.loads(source.read_text())
    destination.write_text(json.dumps(prepare_schema(schema), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

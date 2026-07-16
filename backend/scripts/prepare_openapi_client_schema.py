from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

NULLABLE_UNION_MODELS = {
    "BehavioralAccuracyRead",
    "BehavioralApplyProduct",
    "BehavioralModelRead",
    "BehavioralProductEstimate",
    "CalculationRerunCreate",
    "CalculationRunCreate",
    "CalculationRunRead",
    "CalculationRunSummaryRead",
    "CapitalComparisonRead",
    "CapitalProjectionRead",
    "CapitalProjectionSummaryRead",
    "CapitalSummaryRead",
    "MarketDataConnectionCreate",
    "MarketDataConnectionRead",
    "MarketDataConnectionUpdate",
    "QuotaSummaryRead",
    "TestPullRead",
}


def _mark_binary_uploads(node: Any) -> None:
    """Convert OpenAPI 3.1 binary-content strings to ``format: binary``.

    FastAPI emits multipart file fields as ``{"type": "string",
    "contentMediaType": ...}``; typescript-fetch only maps ``format: binary``
    to Blob, so without this the generated upload parameter is a string.
    """
    if isinstance(node, dict):
        if node.get("type") == "string" and "contentMediaType" in node:
            node.pop("contentMediaType")
            node["format"] = "binary"
        for value in node.values():
            _mark_binary_uploads(value)
    elif isinstance(node, list):
        for item in node:
            _mark_binary_uploads(item)


def prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    prepared = deepcopy(schema)
    components = prepared["components"]["schemas"]
    for model_name in NULLABLE_UNION_MODELS:
        components[model_name].pop("additionalProperties", None)
    _mark_binary_uploads(prepared)
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

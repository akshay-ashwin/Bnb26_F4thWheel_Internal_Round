"""Print the OpenAPI spec as canonical JSON (sorted keys, LF, UTF-8) on stdout.

Always exported with SIM_MODE=true so /sim/telemetry is part of the contract. Run through
`uv run fd openapi`, which writes docs/contract/openapi.json from the host side.
"""

import json
import sys

from app.config import get_settings
from app.main import create_app


def canonical_spec() -> str:
    settings = get_settings().model_copy(update={"sim_mode": True})
    spec = create_app(settings).openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.buffer.write(canonical_spec().encode("utf-8"))

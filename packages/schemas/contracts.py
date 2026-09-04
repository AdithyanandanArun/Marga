"""Contract-version constants and shared Pydantic model defaults."""

from pydantic import ConfigDict

CONTRACT_VERSION = "v1"
MODEL_CONFIG = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)

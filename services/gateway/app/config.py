"""Runtime configuration for the gateway."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings kept deliberately small so the service remains deployable alone."""

    world_state_url: str = "http://localhost:8001"

    @property
    def world_state_ws_url(self) -> str:
        """Return the matching websocket base URL for the authoritative service."""
        parsed = urlparse(self.world_state_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def get_settings() -> Settings:
    return Settings(
        world_state_url=os.getenv("WORLD_STATE_URL", "http://localhost:8001").rstrip("/")
    )

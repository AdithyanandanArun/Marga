"""Pseudonym management — rotating session identifiers for simulation nodes."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_PSEUDONYM_BYTES = 16  # 128-bit random pseudonym


@dataclass
class _PseudonymEntry:
    """Tracks the current pseudonym and its creation time."""

    pseudonym: str
    created_mono: float = field(default_factory=time.monotonic)


class PseudonymManager:
    """Generate and rotate privacy-preserving pseudonyms for actors.

    * :meth:`get_pseudonym` returns the current pseudonym, creating one if
      needed.
    * :meth:`rotate` forces a new pseudonym and returns it.
    * :meth:`resolve` maps a pseudonym back to the internal ID (authorized
      internal use only).

    The rotation interval is configurable; calls to ``get_pseudonym`` will
    auto-rotate when the interval has elapsed.

    The internal correlation mapping is kept in-memory and is **never**
    exposed through world-state APIs.
    """

    def __init__(self, *, rotation_interval_s: float = 300.0) -> None:
        self._rotation_interval_s = rotation_interval_s
        # internal_id -> current entry
        self._forward: dict[str, _PseudonymEntry] = {}
        # pseudonym -> internal_id (includes historical mappings for the
        # current session so that in-flight messages can still be resolved)
        self._reverse: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_pseudonym(self, internal_id: str) -> str:
        """Return the current pseudonym for *internal_id*, auto-rotating if stale."""
        with self._lock:
            entry = self._forward.get(internal_id)
            if entry is not None:
                age = time.monotonic() - entry.created_mono
                if age < self._rotation_interval_s:
                    return entry.pseudonym
            # Either no entry or stale — rotate.
            return self._rotate_locked(internal_id)

    def rotate(self, internal_id: str) -> str:
        """Force immediate rotation and return the new pseudonym."""
        with self._lock:
            return self._rotate_locked(internal_id)

    def resolve(self, pseudonym: str) -> str | None:
        """Resolve a pseudonym to its internal ID (authorized internal use only).

        Returns ``None`` if the pseudonym is unknown.
        """
        with self._lock:
            return self._reverse.get(pseudonym)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rotate_locked(self, internal_id: str) -> str:
        new_pseudo = self._generate()
        self._forward[internal_id] = _PseudonymEntry(pseudonym=new_pseudo)
        self._reverse[new_pseudo] = internal_id
        logger.debug("Pseudonym rotated for %s -> %s", internal_id, new_pseudo)
        return new_pseudo

    @staticmethod
    def _generate() -> str:
        """Generate a cryptographically random pseudonym string."""
        raw = secrets.token_bytes(_PSEUDONYM_BYTES)
        return hashlib.sha256(raw).hexdigest()[:32]

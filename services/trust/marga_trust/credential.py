"""Credential verification — allow-list-based identity and emergency privilege checks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from marga_schemas.trust import TrustLevel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CredentialRecord:
    """An entry in the credential allow-list."""

    sender_id: str
    trust_level: TrustLevel
    emergency_roles: frozenset[str] = frozenset()
    expires_at: datetime | None = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at


class CredentialVerifier:
    """Prototype credential verifier backed by an in-memory allow-list.

    * ``verify_identity`` returns the trust level for a known sender or
      :attr:`TrustLevel.UNTRUSTED` for unknowns.
    * ``verify_emergency`` checks that the sender has been *pre-authorized*
      for the given emergency role.  A payload field alone cannot grant
      emergency privileges — the sender must be in the allow-list with the
      matching role.
    * Expired credentials are treated as absent.

    The allow-list can be populated programmatically or loaded from a dict
    via :meth:`load_from_dict`.
    """

    def __init__(self) -> None:
        self._credentials: dict[str, CredentialRecord] = {}

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def register(self, record: CredentialRecord) -> None:
        """Add or replace a credential record."""
        self._credentials[record.sender_id] = record

    def load_from_dict(self, entries: list[dict]) -> None:
        """Bulk-load credentials from a list of dicts.

        Each dict should have ``sender_id``, ``trust_level`` (string name),
        and optionally ``emergency_roles`` (list of strings) and
        ``expires_at`` (ISO-8601 string or ``None``).
        """
        for entry in entries:
            expires_raw = entry.get("expires_at")
            expires_at = None
            if expires_raw is not None:
                expires_at = datetime.fromisoformat(expires_raw)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)

            record = CredentialRecord(
                sender_id=entry["sender_id"],
                trust_level=TrustLevel(entry["trust_level"]),
                emergency_roles=frozenset(entry.get("emergency_roles", [])),
                expires_at=expires_at,
            )
            self.register(record)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_identity(self, sender_id: str) -> TrustLevel:
        """Return the trust level for *sender_id*, or ``UNTRUSTED`` if unknown/expired."""
        record = self._credentials.get(sender_id)
        if record is None:
            return TrustLevel.UNTRUSTED
        if record.is_expired:
            logger.info("Credential expired for %s", sender_id)
            return TrustLevel.UNTRUSTED
        return record.trust_level

    def verify_emergency(self, sender_id: str, credential_ref: str | None) -> bool:
        """Check whether *sender_id* holds the emergency role named by *credential_ref*.

        Emergency privileges are **never** inferred from payload alone — the
        sender must appear in the allow-list with the matching role.
        """
        if credential_ref is None:
            return False
        record = self._credentials.get(sender_id)
        if record is None:
            logger.warning(
                "Emergency check failed: unknown sender %s for role %s",
                sender_id,
                credential_ref,
            )
            return False
        if record.is_expired:
            logger.warning("Emergency check failed: expired credential for %s", sender_id)
            return False
        if credential_ref not in record.emergency_roles:
            logger.warning(
                "Emergency check failed: sender %s lacks role %s (has %s)",
                sender_id,
                credential_ref,
                record.emergency_roles,
            )
            return False
        return True

    def has_sender(self, sender_id: str) -> bool:
        """Return whether the sender is in the allow-list (even if expired)."""
        return sender_id in self._credentials

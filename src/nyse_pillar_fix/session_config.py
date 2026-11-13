"""Logon RawData (96) session settings (spec section 7.2).

RawData is a 3-byte string:

* byte 1 - Cancel on Disconnect: ``0`` disabled, ``1`` cancel Day/Directed, ``2`` cancel all
* byte 2 - Order Priority Update Acks: ``0`` not subscribed, ``1`` subscribed
* byte 3 - session default Self-Trade Prevention: ``T``, ``N``, ``O``, ``C`` or ``D``

Example: ``"11C"``. If included, all three positions must be populated, and
RawDataLength (95) must be ``3``. Cancel on Disconnect can only be *upgraded* via Logon
(0 -> 1/2, 1 -> 2); downgrades require NYSE Market Support.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import CancelOnDisconnect, PriorityUpdateAcks, SessionSTP


@dataclass(frozen=True)
class SessionSettings:
    cancel_on_disconnect: CancelOnDisconnect = CancelOnDisconnect.DISABLED
    priority_update_acks: PriorityUpdateAcks = PriorityUpdateAcks.NOT_SUBSCRIBED
    self_trade_prevention: SessionSTP = SessionSTP.NONE

    def encode(self) -> str:
        return (
            CancelOnDisconnect(self.cancel_on_disconnect).value
            + PriorityUpdateAcks(self.priority_update_acks).value
            + SessionSTP(self.self_trade_prevention).value
        )

    @classmethod
    def decode(cls, raw: str) -> SessionSettings:
        if len(raw) != 3:
            raise ValueError(f"RawData must be exactly 3 characters, got {raw!r}")
        try:
            return cls(CancelOnDisconnect(raw[0]), PriorityUpdateAcks(raw[1]), SessionSTP(raw[2]))
        except ValueError as e:
            raise ValueError(f"invalid RawData {raw!r}: {e}") from None

    def is_upgrade_from(self, current: SessionSettings) -> bool:
        """Whether this Cancel-on-Disconnect setting is permitted given the *current* default.

        Logon may raise Cancel on Disconnect (0 -> 1 or 2, 1 -> 2) but never lower it.
        """
        return int(self.cancel_on_disconnect.value) >= int(current.cancel_on_disconnect.value)

"""Mapping gateway OrderID / DealID values to NYSE XDP market data (spec section 4).

The gateway sends OrderID (37) and DealID (9483) as decimal strings of unsigned
8-byte little-endian integers.

* OrderID - all 8 bytes equal the XDP ``OrderID``.
* DealID - laid out (little-endian) as::

      offset 0  size 1  Reserved (0)
      offset 1  size 1  SystemID   (Symbol Index Mapping ID)
      offset 2  size 2  MarketID
      offset 4  size 4  TradeID    (public XDP TradeID)
"""

from __future__ import annotations

from dataclasses import dataclass

_U64_MAX = (1 << 64) - 1


def _parse_u64(value: str | int) -> int:
    n = int(value)
    if not 0 <= n <= _U64_MAX:
        raise ValueError(f"{value!r} is not an unsigned 64-bit integer")
    return n


@dataclass(frozen=True)
class DealIDParts:
    reserved: int
    system_id: int
    market_id: int
    trade_id: int

    def to_deal_id(self) -> int:
        return (
            (self.reserved & 0xFF)
            | (self.system_id & 0xFF) << 8
            | (self.market_id & 0xFFFF) << 16
            | (self.trade_id & 0xFFFFFFFF) << 32
        )


def decode_deal_id(deal_id: str | int) -> DealIDParts:
    """Split a gateway DealID into its XDP components."""
    raw = _parse_u64(deal_id).to_bytes(8, "little")
    return DealIDParts(
        reserved=raw[0],
        system_id=raw[1],
        market_id=int.from_bytes(raw[2:4], "little"),
        trade_id=int.from_bytes(raw[4:8], "little"),
    )


def xdp_trade_id(deal_id: str | int) -> int:
    """The public XDP TradeID carried in bytes 5-8 of a DealID."""
    return decode_deal_id(deal_id).trade_id


def xdp_order_id(order_id: str | int) -> int:
    """The XDP OrderID for a gateway OrderID (identical 8-byte value)."""
    return _parse_u64(order_id)

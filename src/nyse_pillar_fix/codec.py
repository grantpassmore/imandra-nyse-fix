"""FIX 4.2 tag=value encoding, decoding and stream framing."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from decimal import Decimal

from . import tags as T

SOH = b"\x01"
BEGIN_STRING = "FIX.4.2"

_HEADER_ORDER = {tag: i for i, tag in enumerate(T.HEADER_TAGS)}


class FixDecodeError(ValueError):
    """Raised when bytes cannot be decoded as a well-formed FIX message."""


class Message:
    """An ordered collection of FIX ``(tag, value)`` pairs.

    Values are stored as ``str`` exactly as they appear on the wire. ``BeginString``,
    ``BodyLength`` and ``CheckSum`` are computed by :meth:`encode` and are never stored.
    Header fields are always emitted before body fields regardless of insertion order;
    body fields keep their insertion order (repeating groups depend on it).
    """

    __slots__ = ("_fields",)

    def __init__(self, msg_type: str | None = None, fields: Iterable[tuple[int, object]] = ()) -> None:
        self._fields: list[tuple[int, str]] = []
        if msg_type is not None:
            self.set(T.MsgType, msg_type)
        for tag, value in fields:
            self.append(tag, value)

    # -- access --------------------------------------------------------------
    @property
    def msg_type(self) -> str:
        return self.get(T.MsgType, "")

    @property
    def seq_num(self) -> int:
        return int(self.get(T.MsgSeqNum, "0"))

    @property
    def poss_dup(self) -> bool:
        return self.get(T.PossDupFlag) == "Y"

    def get(self, tag: int, default: str | None = None) -> str | None:
        for t, v in self._fields:
            if t == tag:
                return v
        return default

    def get_all(self, tag: int) -> list[str]:
        return [v for t, v in self._fields if t == tag]

    def __getitem__(self, tag: int) -> str:
        value = self.get(tag)
        if value is None:
            raise KeyError(tag)
        return value

    def __contains__(self, tag: object) -> bool:
        return any(t == tag for t, _ in self._fields)

    def __iter__(self) -> Iterator[tuple[int, str]]:
        return iter(self._fields)

    def __len__(self) -> int:
        return len(self._fields)

    def tags(self) -> list[int]:
        return [t for t, _ in self._fields]

    def to_dict(self) -> dict[int, str]:
        return {t: v for t, v in self._fields}

    # -- mutation ------------------------------------------------------------
    def append(self, tag: int, value: object) -> Message:
        if value is None:
            return self
        if tag in (T.BeginString, T.BodyLength, T.CheckSum):
            return self
        self._fields.append((int(tag), format_value(value)))
        return self

    def set(self, tag: int, value: object) -> Message:
        """Replace the first occurrence of *tag* (or append it). ``None`` removes it."""
        if value is None:
            self.remove(tag)
            return self
        text = format_value(value)
        for i, (t, _) in enumerate(self._fields):
            if t == tag:
                self._fields[i] = (tag, text)
                return self
        return self.append(tag, text)

    def remove(self, tag: int) -> Message:
        self._fields = [(t, v) for t, v in self._fields if t != tag]
        return self

    def copy(self) -> Message:
        m = Message()
        m._fields = list(self._fields)
        return m

    # -- wire ----------------------------------------------------------------
    def ordered_fields(self) -> list[tuple[int, str]]:
        header = [(t, v) for t, v in self._fields if t in _HEADER_ORDER]
        header.sort(key=lambda tv: _HEADER_ORDER[tv[0]])
        body = [(t, v) for t, v in self._fields if t not in _HEADER_ORDER]
        return header + body

    def encode(self) -> bytes:
        if T.MsgType not in self:
            raise ValueError("MsgType (35) is required")
        body = b"".join(f"{t}={v}".encode("ascii") + SOH for t, v in self.ordered_fields())
        head = f"8={BEGIN_STRING}".encode("ascii") + SOH + f"9={len(body)}".encode("ascii") + SOH
        payload = head + body
        return payload + f"10={checksum(payload):03d}".encode("ascii") + SOH

    @classmethod
    def decode(cls, data: bytes, *, verify: bool = True) -> Message:
        return decode(data, verify=verify)

    def __repr__(self) -> str:
        return "Message(" + "|".join(f"{t}={v}" for t, v in self.ordered_fields()) + ")"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Message) and self.ordered_fields() == other.ordered_fields()


def checksum(data: bytes) -> int:
    """FIX CheckSum: sum of all bytes modulo 256."""
    return sum(data) % 256


def format_value(value: object) -> str:
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, float):
        return format_decimal(Decimal(repr(value)))
    if isinstance(value, datetime):
        return format_utc_timestamp(value)
    if hasattr(value, "value") and isinstance(value.value, str):  # Enum
        return value.value
    text = str(value)
    if "\x01" in text:
        raise ValueError("FIX field values may not contain SOH")
    return text


def format_decimal(value: Decimal) -> str:
    """Render a Decimal in plain notation without trailing zeros (``Decimal('1E+2')`` -> ``100``)."""
    if not value.is_finite():
        raise ValueError(f"non-finite decimal {value}")
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_utc_timestamp(dt: datetime | None = None, *, millis: bool = True) -> str:
    """``YYYYMMDD-HH:MM:SS.mmm`` (or seconds-only when ``millis=False``) in UTC."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    dt = dt.astimezone(timezone.utc)
    base = dt.strftime("%Y%m%d-%H:%M:%S")
    return f"{base}.{dt.microsecond // 1000:03d}" if millis else base


_TS_RE = re.compile(r"^(\d{8})-(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?$")


def parse_utc_timestamp(text: str) -> datetime:
    """Parse ``YYYYMMDD-HH:MM:SS[.fff…]`` (milli- to nanosecond precision) to an aware datetime.

    Sub-microsecond digits are truncated, since :class:`datetime` cannot hold them.
    """
    m = _TS_RE.match(text.replace("‐", "-"))
    if not m:
        raise ValueError(f"invalid UTC timestamp {text!r}")
    date, hh, mm, ss, frac = m.groups()
    micros = int((frac or "0").ljust(6, "0")[:6])
    return datetime(
        int(date[:4]), int(date[4:6]), int(date[6:8]), int(hh), int(mm), int(ss), micros, tzinfo=timezone.utc
    )


def decode(data: bytes, *, verify: bool = True) -> Message:
    """Decode exactly one complete FIX message."""
    if not data.endswith(SOH):
        raise FixDecodeError("message does not end with SOH")
    parts = data[:-1].split(SOH)
    pairs: list[tuple[int, str]] = []
    for part in parts:
        tag, sep, value = part.partition(b"=")
        if not sep or not tag.isdigit():
            raise FixDecodeError(f"malformed field {part!r}")
        pairs.append((int(tag), value.decode("ascii", errors="replace")))
    if len(pairs) < 4 or pairs[0][0] != T.BeginString or pairs[1][0] != T.BodyLength or pairs[2][0] != T.MsgType:
        raise FixDecodeError("message must start with 8, 9, 35")
    if pairs[-1][0] != T.CheckSum:
        raise FixDecodeError("message must end with CheckSum (10)")
    if verify:
        if pairs[0][1] != BEGIN_STRING:
            raise FixDecodeError(f"unexpected BeginString {pairs[0][1]!r}")
        body_start = data.index(SOH, data.index(SOH) + 1) + 1
        trailer_start = data.rindex(b"10=")
        if not pairs[1][1].isdigit() or int(pairs[1][1]) != trailer_start - body_start:
            raise FixDecodeError(f"BodyLength {pairs[1][1]} does not match actual {trailer_start - body_start}")
        expected = checksum(data[:trailer_start])
        if not pairs[-1][1].isdigit() or int(pairs[-1][1]) != expected:
            raise FixDecodeError(f"CheckSum {pairs[-1][1]} does not match computed {expected:03d}")
    msg = Message()
    msg._fields = [(t, v) for t, v in pairs[2:-1]]
    return msg


class FixParser:
    """Incremental parser that frames FIX messages out of an arbitrary byte stream."""

    _HEAD = re.compile(rb"8=FIX\.4\.2\x019=(\d{1,7})\x01")

    def __init__(self, *, verify: bool = True) -> None:
        self._buf = bytearray()
        self._verify = verify

    def feed(self, data: bytes) -> list[Message]:
        self._buf.extend(data)
        out: list[Message] = []
        while True:
            raw = self._next_frame()
            if raw is None:
                return out
            out.append(decode(raw, verify=self._verify))

    def feed_raw(self, data: bytes) -> list[bytes]:
        """Like :meth:`feed` but return the raw framed messages."""
        self._buf.extend(data)
        out: list[bytes] = []
        while (raw := self._next_frame()) is not None:
            out.append(raw)
        return out

    def _next_frame(self) -> bytes | None:
        start = self._buf.find(b"8=FIX")
        if start < 0:
            # keep a possible partial prefix
            del self._buf[: max(0, len(self._buf) - 4)]
            return None
        if start:
            del self._buf[:start]
        m = self._HEAD.match(self._buf)
        if m is None:
            if len(self._buf) > 32 and SOH in self._buf[:32]:
                # Not a FIX 4.2 header; skip this candidate and resync.
                del self._buf[:1]
                return self._next_frame()
            return None
        end = m.end() + int(m.group(1)) + 7  # "10=XXX\x01"
        if len(self._buf) < end:
            return None
        raw = bytes(self._buf[:end])
        del self._buf[:end]
        return raw

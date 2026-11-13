from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nyse_pillar_fix import tags as T
from nyse_pillar_fix.codec import FixDecodeError, FixParser, Message, decode, format_decimal, parse_utc_timestamp

SOH = "\x01"


def wire(s: str) -> bytes:
    return s.replace("|", SOH).encode()


def test_encode_computes_body_length_and_checksum():
    m = Message(
        "0",
        [(T.MsgSeqNum, 2), (T.SenderCompID, "ABC"), (T.TargetCompID, "XNYS"), (T.SendingTime, "20260101-12:00:00.000")],
    )
    raw = m.encode()
    body = wire("35=0|34=2|49=ABC|52=20260101-12:00:00.000|56=XNYS|")
    head = wire(f"8=FIX.4.2|9={len(body)}|")
    expected_sum = sum(head + body) % 256
    assert raw == head + body + wire(f"10={expected_sum:03d}|")


def test_header_fields_are_emitted_before_body_in_canonical_order():
    m = Message("D")
    m.append(T.ClOrdID, "X")
    m.append(T.OnBehalfOfCompID, "ABCD")
    m.append(T.MsgSeqNum, 7)
    raw = m.encode().decode()
    assert raw.index("34=7") < raw.index("115=ABCD") < raw.index("11=X")


def test_roundtrip_and_repeated_tags():
    m = Message("D", [(T.ClOrdID, "A"), (T.NoTradingSessions, 1), (T.TradingSessionID, "2")])
    d = decode(m.encode())
    assert d == m
    assert d.msg_type == "D" and d[T.ClOrdID] == "A"


def test_decode_rejects_bad_checksum_and_body_length():
    raw = Message("0", [(T.MsgSeqNum, 1)]).encode()
    bad_sum = raw[:-4] + b"999" + raw[-1:]
    with pytest.raises(FixDecodeError, match="CheckSum"):
        decode(bad_sum)
    bad_len = raw.replace(b"9=", b"9=1", 1)
    with pytest.raises(FixDecodeError):
        decode(bad_len)
    assert decode(bad_sum, verify=False).msg_type == "0"


def test_parser_frames_partial_and_concatenated_messages():
    a = Message("0", [(T.MsgSeqNum, 1)]).encode()
    b = Message("1", [(T.MsgSeqNum, 2), (T.TestReqID, "T")]).encode()
    stream = b"garbage" + a + b
    p = FixParser()
    out = []
    for i in range(0, len(stream), 5):
        out += p.feed(stream[i : i + 5])
    assert [m.msg_type for m in out] == ["0", "1"]
    assert out[1][T.TestReqID] == "T"


def test_value_formatting():
    assert format_decimal(Decimal("150.2500")) == "150.25"
    assert format_decimal(Decimal("1E+2")) == "100"
    assert format_decimal(Decimal("0.000001")) == "0.000001"
    m = Message("D", [(T.LocateReqd, False), (T.Price, Decimal("10.10"))])
    assert m[T.LocateReqd] == "N" and m[T.Price] == "10.1"
    with pytest.raises(ValueError):
        Message("D", [(T.Text, "a\x01b")])


def test_timestamps():
    dt = datetime(2026, 8, 14, 13, 30, 1, 123456, tzinfo=timezone.utc)
    m = Message("D", [(T.TransactTime, dt)])
    assert m[T.TransactTime] == "20260814-13:30:01.123"
    ns = parse_utc_timestamp("20260814-13:30:01.123456789")
    assert ns == dt
    assert parse_utc_timestamp("20260814-13:30:01") == dt.replace(microsecond=0)

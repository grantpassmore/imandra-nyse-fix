import pytest

from nyse_pillar_fix.enums import CancelOnDisconnect, PriorityUpdateAcks, SessionSTP
from nyse_pillar_fix.session_config import SessionSettings
from nyse_pillar_fix.xdp import DealIDParts, decode_deal_id, xdp_order_id, xdp_trade_id


def test_raw_data_example_from_spec():
    # Section 7.2 example "11C". The example prose says "cancel all orders", but the
    # value table defines byte 1 = 1 as "Cancel - Day; Directed Orders"; we follow the table.
    s = SessionSettings.decode("11C")
    assert s.cancel_on_disconnect is CancelOnDisconnect.CANCEL_DAY_DIRECTED
    assert s.priority_update_acks is PriorityUpdateAcks.SUBSCRIBED
    assert s.self_trade_prevention is SessionSTP.CANCEL_BOTH
    assert s.encode() == "11C"


@pytest.mark.parametrize("raw", ["", "11", "31C", "12C", "11X", "11CC"])
def test_raw_data_rejects_invalid(raw):
    with pytest.raises(ValueError):
        SessionSettings.decode(raw)


def test_cancel_on_disconnect_can_only_be_upgraded():
    cur = SessionSettings(CancelOnDisconnect.CANCEL_DAY_DIRECTED)
    assert SessionSettings(CancelOnDisconnect.CANCEL_ALL).is_upgrade_from(cur)
    assert not SessionSettings(CancelOnDisconnect.DISABLED).is_upgrade_from(cur)


def test_deal_id_layout_is_little_endian():
    raw = bytes([0x00, 0x07, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12])
    deal_id = str(int.from_bytes(raw, "little"))
    parts = decode_deal_id(deal_id)
    assert parts == DealIDParts(reserved=0, system_id=7, market_id=0x1234, trade_id=0x12345678)
    assert xdp_trade_id(deal_id) == 0x12345678
    assert parts.to_deal_id() == int(deal_id)


def test_order_id_maps_directly():
    assert xdp_order_id("18446744073709551615") == 2**64 - 1
    with pytest.raises(ValueError):
        xdp_order_id(str(2**64))

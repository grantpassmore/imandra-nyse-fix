import asyncio
from decimal import Decimal

import pytest

from nyse_pillar_fix import tags as T
from nyse_pillar_fix.client import ClOrdIDGenerator, PillarClient
from nyse_pillar_fix.codec import Message
from nyse_pillar_fix.enums import BulkCancelCode, ExecType, Market, OrdStatus, Side
from nyse_pillar_fix.messages import ExecutionReport, parse_reason_code
from nyse_pillar_fix.session import SessionConfig
from nyse_pillar_fix.testing import MockPillarGateway
from nyse_pillar_fix.validation import ValidationError


def make_client(gw, **kw):
    cfg = SessionConfig(
        sender_comp_id="TEST1", target_comp_id=gw.market, username="TEST1", password="secret", endpoints=[gw.endpoint]
    )
    return PillarClient(cfg, mpid="ABCD", **kw)


async def test_new_order_ack_cancel_flow():
    async with MockPillarGateway(Market.ARCA) as gw:
        reports = []
        async with make_client(gw, on_execution_report=reports.append) as client:
            order = await client.new_order("IBM", Side.BUY, 100, price="150.25")
            await order.wait_status(OrdStatus.NEW, timeout=2)
            assert order.order_id is not None
            sent = gw.active.received[-1]
            assert sent.msg_type == "D" and sent[T.OnBehalfOfCompID] == "ABCD" and sent[T.Price] == "150.25"

            cxl_id = await client.cancel(order)
            await order.wait_status(OrdStatus.CANCELED, timeout=2)
            cxl = [m for m in gw.active.received if m.msg_type == "F"][0]
            assert cxl[T.OrigClOrdID] == order.cl_ord_id and cxl[T.Side] == "1" and cxl[T.Symbol] == "IBM"
            assert cxl[T.ClOrdID] == cxl_id
            assert [r.exec_type for r in reports] == [ExecType.NEW, ExecType.CANCELED]
        assert gw.session_rejects == 0


async def test_replace_updates_order_identity():
    async with MockPillarGateway(Market.NYSE) as gw:
        async with make_client(gw) as client:
            order = await client.new_order("IBM", Side.SELL, 300, price=Decimal("10"))
            await order.wait_status(OrdStatus.NEW, timeout=2)
            old = order.cl_ord_id
            new_id = await client.replace(order, order_qty=200)
            await order.wait_for(lambda o: o.cl_ord_id == new_id, timeout=2)
            assert order.request.order_qty == 200
            assert client.orders[new_id] is order and client.orders[old] is order
            g = [m for m in gw.active.received if m.msg_type == "G"][0]
            assert g[T.OrigClOrdID] == old and g[T.OrderQty] == "200" and g[T.Symbol] == "IBM"


async def test_cancel_reject_for_unknown_order():
    async with MockPillarGateway() as gw:
        rejects = []
        async with make_client(gw, on_cancel_reject=rejects.append) as client:
            order = await client.new_order("IBM", Side.BUY, 100, price="1")
            await order.wait_status(OrdStatus.NEW, timeout=2)
            gw.active.received.clear()  # make the mock forget the order
            await client.cancel(order)
            await order.wait_for(lambda o: o.cancel_rejects, timeout=2)
            assert rejects[0].reason_code == "033"


async def test_bulk_cancel_message():
    async with MockPillarGateway() as gw:
        async with make_client(gw) as client:
            await client.bulk_cancel(BulkCancelCode.MPID_DAY, side=Side.SELL)
            await asyncio.sleep(0.05)
            f = [m for m in gw.active.received if m.msg_type == "F"][0]
            assert f[T.OrderID] == "8" and f[T.Side] == "2" and T.OrigClOrdID not in f
        assert gw.session_rejects == 0


async def test_invalid_order_is_not_sent_and_does_not_consume_seq():
    async with MockPillarGateway(Market.NYSE) as gw:
        async with make_client(gw) as client:
            seq = client.session.store.next_sender_seq
            with pytest.raises(ValidationError):
                await client.new_order("IBM", Side.CROSS, 100, price="1")  # Texas only
            assert client.session.store.next_sender_seq == seq
            assert not client.orders


async def test_fill_tracking():
    async with MockPillarGateway() as gw:
        async with make_client(gw) as client:
            order = await client.new_order("IBM", Side.BUY, 100, price="10")
            await order.wait_status(OrdStatus.NEW, timeout=2)
            d = [m for m in gw.active.received if m.msg_type == "D"][0]
            await gw.active.send(
                gw.execution_report(
                    d,
                    "1",
                    "1",
                    cum_qty=40,
                    leaves_qty=60,
                    last_qty=40,
                    last_px="10",
                    tags={T.DealID: "123", T.LiquidityIndicator: "A"},
                )
            )
            await gw.active.send(
                gw.execution_report(d, "2", "2", cum_qty=100, leaves_qty=0, last_qty=60, last_px="9.5")
            )
            await order.wait_status(OrdStatus.FILLED, timeout=2)
            assert order.cum_qty == 100 and order.leaves_qty == 0
            assert order.avg_px == Decimal("9.7")
            assert order.reports[1].is_fill and order.reports[1].deal_id == "123"


def test_cl_ord_id_generator_is_unique_and_bounded():
    gen = ClOrdIDGenerator("ABCDEF")
    ids = {gen() for _ in range(5000)}
    assert len(ids) == 5000 and all(len(i) <= 20 for i in ids)


def test_reason_code_parsing():
    assert parse_reason_code("R076: System not available") == ("076", "System not available")
    assert parse_reason_code("free text") == (None, "free text")
    assert parse_reason_code(None) == (None, None)


def test_execution_report_parsing():
    m = Message(
        "8",
        [
            (T.ClOrdID, "A"),
            (T.ExecType, "C"),
            (T.OrdStatus, "C"),
            (T.LastQty, "100"),
            (T.LastPx, "12.34"),
            (T.ExecTransType, "0"),
            (T.TransactTime, "20260814-13:30:01.123"),
            (T.UnsolicitedAckType, "2"),
        ],
    )
    er = ExecutionReport.from_message(m)
    assert er.exec_type is ExecType.BILLABLE_CANCEL and er.last_px == Decimal("12.34")
    assert er.transact_time.microsecond == 123000
    assert er.is_priority_update and not er.is_fill


# --- Regression tests for findings from ImandraX (order fill accounting) ---------------


def _fill_state():
    from nyse_pillar_fix.client import OrderState

    req = NewOrderSingleForTest()
    return OrderState(request=req, cl_ord_id=req.cl_ord_id)


def NewOrderSingleForTest():
    from nyse_pillar_fix.messages import NewOrderSingle, Routing

    return NewOrderSingle(
        cl_ord_id="O1", symbol="IBM", side=Side.BUY, order_qty=10_000, price="200", routing=Routing(mpid="ABCD")
    )


def _er(trans, exec_id, last_qty, last_px, cum_qty, ref=None, exec_type="1"):
    fields = [
        (T.ClOrdID, "O1"),
        (T.ExecTransType, trans),
        (T.ExecType, exec_type),
        (T.OrdStatus, exec_type),
        (T.ExecID, exec_id),
        (T.LastQty, last_qty),
        (T.LastPx, last_px),
        (T.CumQty, cum_qty),
    ]
    if ref is not None:
        fields.append((T.ExecRefID, ref))
    return ExecutionReport.from_message(Message("8", fields))


def _client_with(state):
    cfg = SessionConfig(sender_comp_id="TEST1", target_comp_id=Market.NYSE, username="TEST1", password="pw")
    client = PillarClient(cfg, mpid="ABCD")
    client.orders[state.cl_ord_id] = state
    return client


def test_bust_removes_fill_from_notional_and_avg_px():
    # ImandraX counterexample: Fill q=1237 then Bust of that fill.
    state = _fill_state()
    client = _client_with(state)
    client._apply_report(_er("0", "E1", 1237, "150.25", 1237))
    client._apply_report(_er("1", "E2", 1237, "150.25", 0, ref="E1"))
    assert state.cum_qty == 0
    assert state.notional == 0
    assert state.avg_px is None


def test_bust_then_new_fill_has_correct_avg_px():
    state = _fill_state()
    client = _client_with(state)
    client._apply_report(_er("0", "E1", 100, "10", 100))
    client._apply_report(_er("1", "E2", 100, "10", 0, ref="E1"))
    client._apply_report(_er("0", "E3", 50, "20", 50))
    assert state.avg_px == Decimal("20")


def test_correction_replaces_the_corrected_fill():
    # ImandraX counterexample: Fill 9404, then Correct that fill to 9403.
    state = _fill_state()
    client = _client_with(state)
    client._apply_report(_er("0", "E1", 9404, "10", 9404))
    client._apply_report(_er("2", "E2", 9403, "11", 9403, ref="E1"))
    assert state.cum_qty == 9403
    assert state.notional == Decimal(9403 * 11)
    assert state.avg_px == Decimal("11")


def test_duplicate_exec_id_is_counted_once():
    # ImandraX counterexample: two fill reports with the same ExecID.
    state = _fill_state()
    client = _client_with(state)
    client._apply_report(_er("0", "E1", 841, "10", 841))
    client._apply_report(_er("0", "E1", 841, "10", 841))
    assert state.notional == Decimal(8410)
    assert state.avg_px == Decimal("10")

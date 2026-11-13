import asyncio

import pytest

from nyse_pillar_fix import tags as T
from nyse_pillar_fix.codec import Message
from nyse_pillar_fix.enums import CancelOnDisconnect, Market, PriorityUpdateAcks, SessionSTP
from nyse_pillar_fix.session import FixSession, LogonError, SessionConfig, SessionError, SessionState
from nyse_pillar_fix.session_config import SessionSettings
from nyse_pillar_fix.store import MemoryStore
from nyse_pillar_fix.testing import MockPillarGateway


def config(gw, **kw):
    base = dict(
        sender_comp_id="TEST1",
        target_comp_id=Market.NYSE,
        username="TEST1",
        password="secret",
        endpoints=[gw.endpoint],
        heartbeat_interval=30,
        logon_timeout=3,
    )
    base.update(kw)
    return SessionConfig(**base)


async def until(pred, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met")
        await asyncio.sleep(0.01)


async def test_logon_and_logout():
    async with MockPillarGateway() as gw:
        s = FixSession(
            config(
                gw,
                settings=SessionSettings(
                    CancelOnDisconnect.CANCEL_ALL, PriorityUpdateAcks.SUBSCRIBED, SessionSTP.CANCEL_BOTH
                ),
            )
        )
        resp = await s.connect()
        assert resp.msg_type == "A"
        assert s.state is SessionState.ACTIVE
        assert s.settings.encode() == "21C"
        logon = gw.connections[0].received[0]
        assert logon[T.RawData] == "21C" and logon[T.RawDataLength] == "3"
        assert T.ResetSeqNumFlag not in logon
        out = await s.logout("bye")
        assert out is not None and out[T.SessionStatus] == "0"
        assert s.state is SessionState.DISCONNECTED
        assert s.store.next_sender_seq == 3


async def test_invalid_credentials_raise_logon_error():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw, password="wrong"))
        with pytest.raises(LogonError) as ei:
            await s.connect()
        assert ei.value.session_status == "5"
        assert s.state is SessionState.DISCONNECTED


async def test_failover_to_secondary_endpoint():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw, endpoints=[("127.0.0.1", 1), gw.endpoint], connect_timeout=1))
        await s.connect()
        assert s.endpoint == gw.endpoint
        await s.logout()


async def test_answers_test_request_with_matching_heartbeat():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw))
        await s.connect()
        conn = gw.active
        await conn.send(Message("1", [(T.TestReqID, "PING7")]))
        await until(lambda: any(m.msg_type == "0" and m.get(T.TestReqID) == "PING7" for m in conn.received))
        await s.logout()


async def test_sends_heartbeats_and_test_requests_when_idle():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw, heartbeat_interval=1))
        await s.connect()
        conn = gw.active
        await until(lambda: any(m.msg_type == "0" for m in conn.received), timeout=3)
        # The mock answers our Test Request, so the session should stay up.
        await until(lambda: any(m.msg_type == "1" for m in conn.received), timeout=4)
        await asyncio.sleep(0.2)
        assert s.is_active
        await s.logout()


async def test_inbound_gap_triggers_resend_request_and_recovers():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw))
        received = []
        s.on_app_message = received.append
        await s.connect()
        conn = gw.active
        er1 = gw.execution_report(Message("D", [(T.ClOrdID, "A"), (T.OrderQty, 1)]), "0", "0")
        er2 = gw.execution_report(Message("D", [(T.ClOrdID, "B"), (T.OrderQty, 1)]), "0", "0")
        await conn.send(er1)  # stored but we will pretend it was lost
        s.store.set_next_target_seq(s.store.next_target_seq - 1)  # simulate loss of er1
        received.clear()
        await conn.send(er2)  # arrives with seq > expected -> resend request
        await until(lambda: any(m.msg_type == "2" for m in conn.received))
        rr = [m for m in conn.received if m.msg_type == "2"][-1]
        assert rr[T.EndSeqNo] == "0"
        await until(lambda: [r[T.ClOrdID] for r in received] == ["A", "B"])
        assert all(r.poss_dup for r in received)
        await s.logout()


async def test_too_low_sequence_without_possdup_disconnects():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw))
        await s.connect()
        conn = gw.active
        await conn.send(Message("0"), seq=1)
        await until(lambda: s.state is SessionState.DISCONNECTED)
        assert "too low" in s.disconnect_reason


async def test_possdup_with_low_sequence_is_ignored():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw))
        await s.connect()
        conn = gw.active
        await conn.send(Message("0", [(T.PossDupFlag, "Y")]), seq=1)
        await asyncio.sleep(0.1)
        assert s.is_active
        await s.logout()


async def test_gateway_resend_request_is_gap_filled():
    async with MockPillarGateway() as gw:
        store = MemoryStore(next_sender_seq=10)  # gateway expects 1 -> it will ask us to resend
        s = FixSession(config(gw), store)
        await s.connect()
        conn = gw.active
        await until(lambda: any(m.msg_type == "4" for m in conn.received))
        gf = [m for m in conn.received if m.msg_type == "4"][0]
        assert gf[T.GapFillFlag] == "Y" and gf[T.PossDupFlag] == "Y"
        assert gf.seq_num == 1 and gf[T.NewSeqNo] == "11"
        await asyncio.sleep(0.05)
        assert conn.account.next_in == 11
        await s.logout()
        assert gw.session_rejects == 0


async def test_logon_with_low_sequence_is_rejected_then_recovers():
    async with MockPillarGateway() as gw:
        gw.account("TEST1").next_in = 50
        s = FixSession(config(gw))
        with pytest.raises(LogonError) as ei:
            await s.connect()
        assert ei.value.next_expected == 50
        s.store.set_next_sender_seq(ei.value.next_expected)
        await s.connect()
        assert s.is_active
        await s.logout()


def test_logon_response_with_higher_expected_advances_sender_seq():
    s = FixSession(SessionConfig("TEST1", Market.NYSE, "TEST1", "pw"))
    s._on_logon(Message("A", [(T.NextExpectedMsgSeqNum, 40)]))
    assert s.store.next_sender_seq == 40


async def test_application_send_requires_active_session():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw))
        with pytest.raises(SessionError):
            await s.send(Message("D"))


async def test_unsolicited_logout_disconnects():
    async with MockPillarGateway() as gw:
        reasons = []
        s = FixSession(config(gw), on_disconnect=reasons.append)
        await s.connect()
        await gw.active.send(
            Message("5", [(T.SessionStatus, "4"), (T.Text, "Session logout complete"), (T.NextExpectedMsgSeqNum, 2)])
        )
        await until(lambda: s.state is SessionState.DISCONNECTED)
        assert reasons and "logout" in reasons[0]


async def test_resend_mode_replays_application_messages_with_possdup():
    async with MockPillarGateway() as gw:
        s = FixSession(config(gw, resend_mode="resend"))
        await s.connect()
        conn = gw.active
        from nyse_pillar_fix.messages import NewOrderSingle, Routing

        order = NewOrderSingle(
            cl_ord_id="R1", symbol="IBM", side="1", order_qty=10, price="5", routing=Routing(mpid="ABCD")
        )
        seq = await s.send(order.to_message())
        await asyncio.sleep(0.05)
        conn.account.next_in = seq  # pretend the gateway lost the order
        conn.received.clear()
        await conn.send(Message("2", [(T.BeginSeqNo, 1), (T.EndSeqNo, 0)]))
        await until(lambda: any(m.msg_type == "D" for m in conn.received))
        replay = [m for m in conn.received if m.msg_type == "D"][0]
        assert replay.poss_dup and replay.seq_num == seq and T.OrigSendingTime in replay
        gap = [m for m in conn.received if m.msg_type == "4"][0]
        assert gap.seq_num == 1 and gap[T.NewSeqNo] == str(seq)
        await s.logout()

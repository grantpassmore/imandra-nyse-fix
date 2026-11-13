"""An in-process mock of the Pillar FIX Gateway, for tests and local development.

It implements the session rules of spec section 7.1 (logon authentication, sequence
checks, resend requests, test requests, logout) and acknowledges orders with simple
Execution Reports. It is not a matching engine.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from . import tags as T
from .codec import FixDecodeError, FixParser, Message, format_utc_timestamp
from .enums import APP_MSG_TYPES, Market, MsgType
from .validation import validate

log = logging.getLogger("nyse_pillar_fix.testing")

#: Optional hook: (gateway, connection, inbound message) -> True if handled.
OrderHandler = Callable[["MockPillarGateway", "GatewayConnection", Message], "bool | None"]


@dataclass
class _Account:
    password: str
    next_in: int = 1  # next MsgSeqNum expected from the client
    next_out: int = 1
    sent: dict[int, bytes] = field(default_factory=dict)  # application messages only
    raw_data: str = "00T"


class GatewayConnection:
    def __init__(self, gw: MockPillarGateway, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.gw = gw
        self.reader = reader
        self.writer = writer
        self.account: _Account | None = None
        self.sender_comp_id: str | None = None
        self.received: list[Message] = []
        self.logged_on = False

    async def send(self, msg: Message, *, seq: int | None = None, store: bool = True) -> int:
        assert self.account is not None and self.sender_comp_id is not None
        acct = self.account
        num = acct.next_out if seq is None else seq
        msg.set(T.MsgSeqNum, num)
        msg.set(T.SenderCompID, self.gw.market.value)
        msg.set(T.TargetCompID, self.sender_comp_id)
        msg.set(T.SendingTime, format_utc_timestamp())
        raw = msg.encode()
        if seq is None:
            acct.next_out += 1
            if msg.msg_type in APP_MSG_TYPES and store:
                acct.sent[num] = raw
        self.writer.write(raw)
        await self.writer.drain()
        return num

    async def send_unauthenticated(self, msg: Message) -> None:
        msg.set(T.MsgSeqNum, 1)
        msg.set(T.SenderCompID, self.gw.market.value)
        msg.set(T.TargetCompID, self.sender_comp_id or "UNKNOWN")
        msg.set(T.SendingTime, format_utc_timestamp())
        self.writer.write(msg.encode())
        await self.writer.drain()

    def close(self) -> None:
        self.writer.close()


class MockPillarGateway:
    def __init__(
        self,
        market: Market = Market.NYSE,
        accounts: dict[str, str] | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        order_handler: OrderHandler | None = None,
    ) -> None:
        self.market = market
        self.host = host
        self.port = port
        self.accounts = {u: _Account(p) for u, p in (accounts or {"TEST1": "secret"}).items()}
        self.order_handler = order_handler
        self.connections: list[GatewayConnection] = []
        self.session_rejects = 0
        self._server: asyncio.base_events.Server | None = None
        self._ids = itertools.count(1)

    @property
    def endpoint(self) -> tuple[str, int]:
        return (self.host, self.port)

    async def start(self) -> MockPillarGateway:
        self._server = await asyncio.start_server(self._serve, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        for c in self.connections:
            c.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def __aenter__(self) -> MockPillarGateway:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    def account(self, username: str) -> _Account:
        return self.accounts[username]

    @property
    def active(self) -> GatewayConnection | None:
        for c in reversed(self.connections):
            if c.logged_on:
                return c
        return None

    # ------------------------------------------------------------------
    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = GatewayConnection(self, reader, writer)
        self.connections.append(conn)
        parser = FixParser()
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for raw in parser.feed_raw(data):
                    try:
                        msg = Message.decode(raw)
                    except FixDecodeError:
                        continue
                    conn.received.append(msg)
                    if not await self._handle(conn, msg):
                        conn.logged_on = False
                        writer.close()
                        return
        except (ConnectionError, OSError):
            pass
        finally:
            conn.logged_on = False

    async def _reject(self, conn: GatewayConnection, msg: Message, reason: str, text: str, ref_tag: int | None = None):
        self.session_rejects += 1
        rej = Message(MsgType.REJECT)
        rej.append(T.RefSeqNum, msg.seq_num)
        rej.append(T.SessionRejectReason, reason)
        if ref_tag is not None:
            rej.append(T.RefTagID, ref_tag)
        rej.append(T.RefMsgType, msg.msg_type)
        rej.append(T.Text, text[:100])
        assert conn.account is not None
        rej.append(T.NextExpectedMsgSeqNum, conn.account.next_in)
        await conn.send(rej)

    async def _handle(self, conn: GatewayConnection, msg: Message) -> bool:
        mt = msg.msg_type
        if not conn.logged_on:
            if mt != MsgType.LOGON:
                return False
            user = msg.get(T.Username, "")
            acct = self.accounts.get(user)
            conn.sender_comp_id = msg.get(T.SenderCompID)
            if acct is None or acct.password != msg.get(T.Password) or user != conn.sender_comp_id:
                lo = Message(MsgType.LOGOUT, [(T.SessionStatus, "5"), (T.Text, "Invalid username or password")])
                lo.append(T.NextExpectedMsgSeqNum, acct.next_in if acct else 1)
                await conn.send_unauthenticated(lo)
                return False
            conn.account = acct
            if msg.get(T.RawData):
                acct.raw_data = msg[T.RawData]
            seq = msg.seq_num
            if seq < acct.next_in and not msg.poss_dup:
                await self._reject(conn, msg, "99", f"MsgSeqNum too low, expecting {acct.next_in}")
                return False
            conn.logged_on = True
            resp = Message(MsgType.LOGON)
            resp.append(T.NextExpectedMsgSeqNum, acct.next_in if seq > acct.next_in else seq + 1)
            resp.append(T.EncryptMethod, 0)
            resp.append(T.HeartBtInt, msg.get(T.HeartBtInt, "30"))
            resp.append(T.RawDataLength, 3)
            resp.append(T.RawData, acct.raw_data)
            resp.append(T.Username, user)
            resp.append(T.SessionStatus, 0)
            if seq > acct.next_in:
                await conn.send(resp)
                await conn.send(Message(MsgType.RESEND_REQUEST, [(T.BeginSeqNo, acct.next_in), (T.EndSeqNo, 0)]))
            else:
                acct.next_in = seq + 1
                await conn.send(resp)
            return True

        acct = conn.account
        assert acct is not None
        seq = msg.seq_num
        if mt == MsgType.SEQUENCE_RESET and msg.get(T.GapFillFlag) != "Y":
            new = int(msg[T.NewSeqNo])
            if new > acct.next_in:
                acct.next_in = new
            return True
        if seq > acct.next_in:
            if mt == MsgType.RESEND_REQUEST:
                await self._resend(conn, msg)
            else:
                await conn.send(Message(MsgType.RESEND_REQUEST, [(T.BeginSeqNo, acct.next_in), (T.EndSeqNo, 0)]))
            return True
        if seq < acct.next_in:
            if msg.poss_dup:
                return True
            await self._reject(conn, msg, "99", f"MsgSeqNum too low, expecting {acct.next_in}")
            return False
        # as expected
        if mt == MsgType.SEQUENCE_RESET:
            acct.next_in = max(int(msg[T.NewSeqNo]), seq + 1)
            return True
        acct.next_in = seq + 1
        if mt == MsgType.HEARTBEAT:
            return True
        if mt == MsgType.TEST_REQUEST:
            await conn.send(Message(MsgType.HEARTBEAT, [(T.TestReqID, msg.get(T.TestReqID, ""))]))
            return True
        if mt == MsgType.RESEND_REQUEST:
            await self._resend(conn, msg)
            return True
        if mt == MsgType.LOGOUT:
            await conn.send(
                Message(
                    MsgType.LOGOUT,
                    [(T.SessionStatus, "0"), (T.Text, "Logout acknowledged"), (T.NextExpectedMsgSeqNum, acct.next_in)],
                )
            )
            return False
        if mt in APP_MSG_TYPES:
            issues = validate(msg, self.market)
            if issues:
                first = issues[0]
                await self._reject(conn, msg, "5", str(first), first.tag)
                return True
            if self.order_handler is not None and self.order_handler(self, conn, msg):
                return True
            await self._default_order_handler(conn, msg)
            return True
        await self._reject(conn, msg, "11", f"Invalid MsgType {mt}")
        return True

    async def _resend(self, conn: GatewayConnection, msg: Message) -> None:
        acct = conn.account
        assert acct is not None
        begin = int(msg[T.BeginSeqNo])
        end = int(msg[T.EndSeqNo]) or acct.next_out - 1
        seq = begin
        while seq <= end:
            raw = acct.sent.get(seq)
            if raw is not None:
                m = Message.decode(raw)
                m.set(T.PossDupFlag, "Y")
                m.set(T.OrigSendingTime, m.get(T.SendingTime))
                conn.writer.write(m.encode())
                seq += 1
                continue
            start = seq
            while seq <= end and seq not in acct.sent:
                seq += 1
            gf = Message(MsgType.SEQUENCE_RESET, [(T.PossDupFlag, "Y"), (T.GapFillFlag, "Y"), (T.NewSeqNo, seq)])
            await conn.send(gf, seq=start)
        await conn.writer.drain()

    # ------------------------------------------------------------------
    def execution_report(
        self,
        order: Message,
        exec_type: str,
        ord_status: str,
        *,
        tags: dict[int, object] | None = None,
        **extra: object,
    ) -> Message:
        er = Message(MsgType.EXECUTION_REPORT)
        for tag in (T.OnBehalfOfCompID, T.OnBehalfOfSubID, T.SenderSubID, T.DeliverToCompID):
            if tag in order:
                er.set(tag, order[tag])
        for tag in (T.ClOrdID, T.OrigClOrdID, T.Symbol, T.Side, T.OrderQty, T.OrdType, T.Price, T.TimeInForce):
            if tag in order:
                er.append(tag, order[tag])
        er.append(T.ExecID, next(self._ids))
        er.append(T.ExecTransType, "0")
        er.append(T.ExecType, exec_type)
        er.append(T.OrdStatus, ord_status)
        er.append(T.OrderID, extra.pop("order_id", 1000 + next(self._ids)))
        er.append(T.CumQty, extra.pop("cum_qty", 0))
        er.append(T.LeavesQty, extra.pop("leaves_qty", order.get(T.OrderQty, "0")))
        er.append(T.LastQty, extra.pop("last_qty", 0))
        er.append(T.LastPx, extra.pop("last_px", 0))
        er.append(T.NoTradingSessions, 1)
        er.append(T.TradingSessionID, order.get(T.TradingSessionID, "2"))
        er.append(T.OrderCapacity, order.get(T.OrderCapacity, "A"))
        er.append(T.FlowIndicator, "0")
        ts = format_utc_timestamp()
        er.append(T.TransactTime, ts)
        er.append(T.NanosecondTransactTime, ts + "000000")
        er.append(T.NanosecondSendingTime, ts + "000000")
        for tag, value in (tags or {}).items():
            er.append(tag, value)
        return er

    async def _default_order_handler(self, conn: GatewayConnection, msg: Message) -> None:
        mt = msg.msg_type
        if mt == MsgType.NEW_ORDER_SINGLE:
            await conn.send(self.execution_report(msg, "0", "0"))
        elif mt == MsgType.ORDER_CANCEL_REPLACE_REQUEST:
            await conn.send(self.execution_report(msg, "5", "5"))
        elif mt == MsgType.ORDER_CANCEL_REQUEST:
            if T.OrderID in msg:  # bulk cancel: nothing to report in the mock
                return
            original = next(
                (m for m in conn.received if m.msg_type in ("D", "G") and m.get(T.ClOrdID) == msg.get(T.OrigClOrdID)),
                None,
            )
            if original is None:
                rej = Message(MsgType.ORDER_CANCEL_REJECT)
                rej.set(T.OnBehalfOfCompID, msg.get(T.OnBehalfOfCompID))
                rej.append(T.ClOrdID, msg[T.ClOrdID])
                rej.append(T.OrigClOrdID, msg.get(T.OrigClOrdID))
                rej.append(T.OrderID, 0)
                rej.append(T.OrdStatus, "8")
                rej.append(T.CxlRejResponseTo, "1")
                rej.append(T.Text, "R033: Unknown order")
                ts = format_utc_timestamp()
                rej.append(T.NanosecondSendingTime, ts + "000000")
                rej.append(T.NanosecondTransactTime, ts + "000000")
                await conn.send(rej)
                return
            er = self.execution_report(original, "4", "4", leaves_qty=0)
            er.set(T.ClOrdID, msg[T.ClOrdID])
            er.set(T.OrigClOrdID, msg[T.OrigClOrdID])
            await conn.send(er)

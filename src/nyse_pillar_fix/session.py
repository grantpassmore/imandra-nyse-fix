"""Asyncio FIX session engine implementing the Pillar session layer (spec section 7)."""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import ssl as _ssl
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from . import tags as T
from .codec import FixDecodeError, FixParser, Message, format_utc_timestamp
from .enums import APP_MSG_TYPES, Market, MsgType, SessionStatus
from .session_config import SessionSettings
from .store import MemoryStore, MessageStore
from .throttle import RollingWindowThrottle
from .validation import ValidationError, validate

log = logging.getLogger("nyse_pillar_fix.session")

Callback = Callable[[Message], "Awaitable[None] | None"]


class SessionError(Exception):
    """A session-level failure (not connected, sequence error, peer timeout...)."""


class LogonError(SessionError):
    """The gateway rejected or did not answer the Logon request."""

    def __init__(self, message: str, session_status: str | None = None, next_expected: int | None = None) -> None:
        super().__init__(message)
        self.session_status = session_status
        #: NextExpectedMsgSeqNum (789) reported by the gateway, when it sent one.
        self.next_expected = next_expected


class SessionState(enum.Enum):
    DISCONNECTED = "disconnected"
    LOGON_SENT = "logon_sent"
    ACTIVE = "active"
    LOGOUT_SENT = "logout_sent"


@dataclass
class SessionConfig:
    sender_comp_id: str
    target_comp_id: Market | str
    username: str
    password: str
    #: (host, port) pairs, tried in order: primary first, then secondary (section 1.3).
    endpoints: Sequence[tuple[str, int]] = field(default_factory=list)
    heartbeat_interval: int = 30
    #: Optional Logon RawData override; ``None`` uses the firm's configured defaults.
    settings: SessionSettings | None = None
    #: Validate outbound messages before sending (raises ValidationError).
    validate: bool = True
    #: How to answer a gateway Resend Request for our application messages:
    #: ``"gap_fill"`` (default; never re-submits stale orders) or ``"resend"`` (PossDupFlag=Y).
    resend_mode: Literal["gap_fill", "resend"] = "gap_fill"
    logon_timeout: float = 10.0
    connect_timeout: float = 5.0
    logout_timeout: float = 5.0
    ssl: _ssl.SSLContext | None = None
    #: Client-side pacing (section 5.2). ``None`` disables it.
    throttle: RollingWindowThrottle | None = field(default_factory=RollingWindowThrottle)

    @property
    def market(self) -> Market | None:
        try:
            return Market(str(getattr(self.target_comp_id, "value", self.target_comp_id)))
        except ValueError:
            return None

    def __post_init__(self) -> None:
        if not 1 <= self.heartbeat_interval <= 60:
            raise ValueError("heartbeat_interval must be 1-60 seconds")
        if len(self.username) > 16 or len(self.password) > 32:
            raise ValueError("username is limited to 16 and password to 32 characters")


class FixSession:
    """One FIX session to the Pillar gateway.

    Callbacks may be plain functions or coroutines:

    * ``on_app_message(msg)`` - Execution Reports and Order Cancel Rejects, in sequence.
    * ``on_session_reject(msg)`` - Session-Level Reject (35=3) for one of our messages.
    * ``on_disconnect(reason)`` - the TCP connection closed.
    """

    def __init__(
        self,
        config: SessionConfig,
        store: MessageStore | None = None,
        *,
        on_app_message: Callback | None = None,
        on_session_reject: Callback | None = None,
        on_disconnect: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        self.config = config
        self.store = store or MemoryStore()
        self.on_app_message = on_app_message
        self.on_session_reject = on_session_reject
        self.on_disconnect = on_disconnect

        self.state = SessionState.DISCONNECTED
        self.endpoint: tuple[str, int] | None = None
        #: Session settings echoed by the gateway in the Logon response RawData.
        self.settings: SessionSettings | None = None
        #: NextExpectedMsgSeqNum (789) from the last Logon response / Logout / Reject.
        self.gateway_next_expected: int | None = None

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._parser = FixParser()
        self._send_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._logon_future: asyncio.Future[Message] | None = None
        self._logout_future: asyncio.Future[Message] | None = None
        self._closed = asyncio.Event()
        self._closed.set()
        self._last_sent = 0.0
        self._last_recv = 0.0
        self._test_req_id: str | None = None
        self._test_req_sent = 0.0
        self._test_req_counter = 0
        self._resend_requested_upto = 0
        self.disconnect_reason: str | None = None

    # ------------------------------------------------------------------ connect
    @property
    def is_active(self) -> bool:
        return self.state is SessionState.ACTIVE

    async def connect(self, endpoints: Sequence[tuple[str, int]] | None = None) -> Message:
        """Open TCP (trying each endpoint in turn), log on, and return the Logon response."""
        if self.state is not SessionState.DISCONNECTED:
            raise SessionError(f"session is {self.state.value}")
        endpoints = list(endpoints or self.config.endpoints)
        if not endpoints:
            raise SessionError("no endpoints configured")
        last_error: Exception | None = None
        for host, port in endpoints:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port, ssl=self.config.ssl), self.config.connect_timeout
                )
            except (OSError, asyncio.TimeoutError) as e:
                log.warning("connect to %s:%s failed: %s", host, port, e)
                last_error = e
                continue
            self.endpoint = (host, port)
            return await self._start(reader, writer)
        raise SessionError(f"could not connect to any endpoint: {last_error}")

    async def _start(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> Message:
        self._reader, self._writer = reader, writer
        self._parser = FixParser()
        self._closed.clear()
        self.disconnect_reason = None
        self._test_req_id = None
        self._resend_requested_upto = 0
        now = time.monotonic()
        self._last_sent = self._last_recv = now
        loop = asyncio.get_running_loop()
        self._logon_future = loop.create_future()
        self._tasks = [loop.create_task(self._read_loop(), name="pillar-fix-reader")]

        logon = Message(MsgType.LOGON)
        logon.append(T.EncryptMethod, 0)
        logon.append(T.HeartBtInt, self.config.heartbeat_interval)
        if self.config.settings is not None:
            logon.append(T.RawDataLength, 3)
            logon.append(T.RawData, self.config.settings.encode())
        logon.append(T.Username, self.config.username)
        logon.append(T.Password, self.config.password)
        self.state = SessionState.LOGON_SENT
        try:
            await self._send(logon)
            response = await asyncio.wait_for(asyncio.shield(self._logon_future), self.config.logon_timeout)
        except asyncio.TimeoutError:
            await self._disconnect("logon timeout")
            raise LogonError("no Logon response from gateway") from None
        except BaseException:
            await self._disconnect("logon failed")
            raise
        self._tasks.append(loop.create_task(self._heartbeat_loop(), name="pillar-fix-heartbeat"))
        return response

    # ------------------------------------------------------------------ sending
    async def send(self, msg: Message) -> int:
        """Send an application message; returns its MsgSeqNum."""
        if msg.msg_type not in APP_MSG_TYPES:
            raise SessionError("use the session methods for session-level messages")
        if not self.is_active:
            raise SessionError(f"cannot send: session is {self.state.value}")
        return await self._send(msg)

    async def _send(self, msg: Message, *, seq: int | None = None, store: bool | None = None) -> int:
        writer = self._writer
        if writer is None:
            raise SessionError("not connected")
        if self.config.validate:
            issues = validate(msg, self.config.market)
            if issues:
                raise ValidationError(issues)
        async with self._send_lock:
            if self.config.throttle is not None:
                await self.config.throttle.acquire()
            assign = seq is None
            num = self.store.next_sender_seq if assign else seq
            msg.set(T.MsgSeqNum, num)
            msg.set(T.SenderCompID, self.config.sender_comp_id)
            msg.set(T.TargetCompID, getattr(self.config.target_comp_id, "value", self.config.target_comp_id))
            msg.set(T.SendingTime, format_utc_timestamp())
            raw = msg.encode()
            if store if store is not None else msg.msg_type in APP_MSG_TYPES:
                self.store.store_sent(num, raw)
            if assign:
                self.store.set_next_sender_seq(num + 1)
            log.debug(">> %r", msg)
            writer.write(raw)
            self._last_sent = time.monotonic()
            await writer.drain()
            return num

    async def _write_raw(self, raw: bytes) -> None:
        if self._writer is None:
            raise SessionError("not connected")
        async with self._send_lock:
            if self.config.throttle is not None:
                await self.config.throttle.acquire()
            self._writer.write(raw)
            self._last_sent = time.monotonic()
            await self._writer.drain()

    async def send_test_request(self, test_req_id: str | None = None) -> str:
        if test_req_id is None:
            self._test_req_counter += 1
            test_req_id = f"TR{self._test_req_counter}"
        self._test_req_id = test_req_id
        self._test_req_sent = time.monotonic()
        await self._send(Message(MsgType.TEST_REQUEST, [(T.TestReqID, test_req_id)]))
        return test_req_id

    async def send_resend_request(self, begin: int, end: int = 0) -> None:
        await self._send(Message(MsgType.RESEND_REQUEST, [(T.BeginSeqNo, begin), (T.EndSeqNo, end)]))

    async def send_sequence_reset(self, new_seq_no: int) -> None:
        """Sequence Reset (GapFillFlag=N) advancing the gateway's expected inbound number."""
        if new_seq_no <= self.store.next_sender_seq - 1:
            raise ValueError("NewSeqNo must exceed the last sent MsgSeqNum")
        msg = Message(MsgType.SEQUENCE_RESET, [(T.GapFillFlag, "N"), (T.NewSeqNo, new_seq_no)])
        await self._send(msg, seq=self.store.next_sender_seq, store=False)
        self.store.set_next_sender_seq(new_seq_no)

    async def logout(self, text: str | None = None) -> Message | None:
        """Send Logout, wait for the gateway's Logout response, then close the connection."""
        if self.state is SessionState.DISCONNECTED:
            return None
        loop = asyncio.get_running_loop()
        self._logout_future = loop.create_future()
        self.state = SessionState.LOGOUT_SENT
        msg = Message(MsgType.LOGOUT)
        if text:
            msg.append(T.Text, text)
        response: Message | None = None
        try:
            await self._send(msg)
            response = await asyncio.wait_for(asyncio.shield(self._logout_future), self.config.logout_timeout)
        except (asyncio.TimeoutError, SessionError, ConnectionError):
            log.warning("no Logout response from gateway")
        await self._disconnect("logout")
        return response

    async def close(self) -> None:
        await self._disconnect("closed by client")

    async def wait_closed(self) -> None:
        await self._closed.wait()

    # ------------------------------------------------------------------ receiving
    async def _read_loop(self) -> None:
        assert self._reader is not None
        reason = "connection closed by peer"
        try:
            while True:
                data = await self._reader.read(65536)
                if not data:
                    break
                self._last_recv = time.monotonic()
                for raw in self._parser.feed_raw(data):
                    try:
                        msg = Message.decode(raw)
                    except FixDecodeError as e:
                        log.warning("ignoring garbled message: %s", e)
                        continue
                    log.debug("<< %r", msg)
                    await self._handle(msg)
                    if self._writer is None:
                        return
        except asyncio.CancelledError:
            raise
        except SessionError as e:
            reason = str(e)
        except (ConnectionError, OSError) as e:
            reason = f"connection error: {e}"
        except Exception as e:  # pragma: no cover - defensive
            log.exception("error in read loop")
            reason = f"internal error: {e}"
        await self._disconnect(reason, from_reader=True)

    async def _handle(self, msg: Message) -> None:
        mt = msg.msg_type
        seq = msg.seq_num
        expected = self.store.next_target_seq

        # Sequence Reset (reset mode) is honoured regardless of MsgSeqNum.
        if mt == MsgType.SEQUENCE_RESET and msg.get(T.GapFillFlag) != "Y":
            new = int(msg.get(T.NewSeqNo, "0"))
            if new > expected:
                self.store.set_next_target_seq(new)
            else:
                log.warning("ignoring Sequence Reset to %s (expected %s)", new, expected)
            return

        if seq > expected:
            log.info("sequence gap: expected %s, received %s", expected, seq)
            if mt == MsgType.LOGON:
                self._on_logon(msg)
            elif mt == MsgType.LOGOUT:
                await self._on_logout(msg)
                return
            elif mt == MsgType.RESEND_REQUEST:
                await self._on_resend_request(msg)
            if self._resend_requested_upto < expected and self._writer is not None:
                self._resend_requested_upto = seq
                await self.send_resend_request(expected, 0)
            else:
                self._resend_requested_upto = max(self._resend_requested_upto, seq)
            return

        if seq < expected:
            if msg.poss_dup:
                return
            if mt == MsgType.LOGOUT:
                await self._on_logout(msg)
                return
            text = f"MsgSeqNum too low - expected {expected} but received {seq}"
            log.error(text)
            if self._logon_future and not self._logon_future.done():
                self._logon_future.set_exception(LogonError(text))
            with contextlib.suppress(SessionError, ConnectionError):
                await self._send(Message(MsgType.LOGOUT, [(T.Text, text)]))
            raise SessionError(text)

        # seq == expected
        if mt == MsgType.SEQUENCE_RESET:  # gap fill
            new = int(msg.get(T.NewSeqNo, "0"))
            self.store.set_next_target_seq(new if new > expected else expected + 1)
            return
        self.store.set_next_target_seq(expected + 1)

        if mt == MsgType.HEARTBEAT:
            if self._test_req_id is not None and msg.get(T.TestReqID) == self._test_req_id:
                self._test_req_id = None
        elif mt == MsgType.TEST_REQUEST:
            await self._send(Message(MsgType.HEARTBEAT, [(T.TestReqID, msg.get(T.TestReqID, ""))]))
        elif mt == MsgType.RESEND_REQUEST:
            await self._on_resend_request(msg)
        elif mt == MsgType.REJECT:
            self._note_next_expected(msg)
            log.warning("session reject: %r", msg)
            if self._logon_future and not self._logon_future.done():
                self._logon_future.set_exception(
                    LogonError(f"logon rejected: {msg.get(T.Text, '')}", None, self.gateway_next_expected)
                )
            await self._dispatch(self.on_session_reject, msg)
        elif mt == MsgType.LOGOUT:
            await self._on_logout(msg)
        elif mt == MsgType.LOGON:
            self._on_logon(msg)
        elif mt in APP_MSG_TYPES:
            await self._dispatch(self.on_app_message, msg)
        else:
            log.warning("unexpected MsgType %r", mt)

    async def _dispatch(self, cb: Callback | None, msg: Message) -> None:
        if cb is None:
            return
        try:
            result = cb(msg)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("callback raised")

    def _note_next_expected(self, msg: Message) -> None:
        nxt = msg.get(T.NextExpectedMsgSeqNum)
        if nxt and nxt.isdigit():
            self.gateway_next_expected = int(nxt)

    def _on_logon(self, msg: Message) -> None:
        self._note_next_expected(msg)
        raw = msg.get(T.RawData)
        if raw:
            try:
                self.settings = SessionSettings.decode(raw)
            except ValueError:
                log.warning("unrecognised RawData in Logon response: %r", raw)
        # The gateway may expect a higher number than we would send next (e.g. our store
        # was lost); sending lower numbers would be rejected, so jump forward.
        if self.gateway_next_expected and self.gateway_next_expected > self.store.next_sender_seq:
            log.warning(
                "gateway expects MsgSeqNum %s; advancing from %s",
                self.gateway_next_expected,
                self.store.next_sender_seq,
            )
            self.store.set_next_sender_seq(self.gateway_next_expected)
        self.state = SessionState.ACTIVE
        if self._logon_future and not self._logon_future.done():
            self._logon_future.set_result(msg)

    async def _on_logout(self, msg: Message) -> None:
        self._note_next_expected(msg)
        status = msg.get(T.SessionStatus)
        text = msg.get(T.Text, "")
        if self._logon_future and not self._logon_future.done():
            reason = "invalid username or password" if status == SessionStatus.INVALID_CREDENTIALS else text
            self._logon_future.set_exception(
                LogonError(f"logon rejected: {reason}", status, self.gateway_next_expected)
            )
        if self._logout_future and not self._logout_future.done():
            self._logout_future.set_result(msg)
        else:
            log.warning("unsolicited logout (SessionStatus=%s): %s", status, text)
        await self._disconnect(f"logout received: {text or status}", from_reader=True)

    async def _on_resend_request(self, msg: Message) -> None:
        begin = int(msg.get(T.BeginSeqNo, "1"))
        end = int(msg.get(T.EndSeqNo, "0"))
        last = self.store.next_sender_seq - 1
        if end == 0 or end > last:
            end = last
        if begin > end:
            return
        log.info("gateway requested resend %s-%s", begin, end)
        stored = dict(self.store.get_sent(begin, end)) if self.config.resend_mode == "resend" else {}
        gap_start: int | None = None
        for seq in range(begin, end + 1):
            raw = stored.get(seq)
            if raw is None:
                if gap_start is None:
                    gap_start = seq
                continue
            if gap_start is not None:
                await self._send_gap_fill(gap_start, seq)
                gap_start = None
            original = Message.decode(raw, verify=False)
            original.set(T.PossDupFlag, "Y")
            original.set(T.OrigSendingTime, original.get(T.SendingTime))
            original.set(T.SendingTime, format_utc_timestamp())
            await self._write_raw(original.encode())
        if gap_start is not None:
            await self._send_gap_fill(gap_start, end + 1)

    async def _send_gap_fill(self, seq: int, new_seq_no: int) -> None:
        msg = Message(
            MsgType.SEQUENCE_RESET,
            [(T.PossDupFlag, "Y"), (T.GapFillFlag, "Y"), (T.NewSeqNo, new_seq_no)],
        )
        await self._send(msg, seq=seq, store=False)

    # ------------------------------------------------------------------ timers
    async def _heartbeat_loop(self) -> None:
        hb = self.config.heartbeat_interval
        tick = min(1.0, hb / 4)
        try:
            while self._writer is not None:
                await asyncio.sleep(tick)
                now = time.monotonic()
                if self._test_req_id is not None and now - self._test_req_sent >= hb:
                    await self._disconnect("no response to Test Request")
                    return
                if self._test_req_id is None and now - self._last_recv >= hb + max(1.0, 0.2 * hb):
                    await self.send_test_request()
                if now - self._last_sent >= hb:
                    await self._send(Message(MsgType.HEARTBEAT))
        except asyncio.CancelledError:
            raise
        except (SessionError, ConnectionError, OSError) as e:
            await self._disconnect(f"heartbeat failed: {e}")

    # ------------------------------------------------------------------ teardown
    async def _disconnect(self, reason: str, *, from_reader: bool = False) -> None:
        if self._writer is None:
            return
        writer, self._writer = self._writer, None
        self.state = SessionState.DISCONNECTED
        self.disconnect_reason = reason
        log.info("disconnected: %s", reason)
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current and not task.done():
                task.cancel()
        self._tasks = []
        for fut in (self._logon_future, self._logout_future):
            if fut and not fut.done():
                fut.set_exception(SessionError(reason))
                fut.exception()  # mark retrieved
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except (OSError, asyncio.TimeoutError, ConnectionError):
            pass
        self._closed.set()
        if self.on_disconnect is not None:
            try:
                result = self.on_disconnect(reason)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("on_disconnect raised")

"""High-level order-entry client for the NYSE Pillar FIX Gateway."""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import logging
import string
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .codec import Message
from .enums import (
    TERMINAL_ORD_STATUSES,
    BulkCancelCode,
    ExecTransType,
    ExecType,
    MsgType,
    OrdStatus,
    OrdType,
    Side,
    TimeInForce,
)
from .messages import (
    ExecutionReport,
    NewOrderSingle,
    OrderCancelReject,
    OrderCancelReplaceRequest,
    OrderCancelRequest,
    Routing,
    _OrderFields,
)
from .session import FixSession, SessionConfig
from .store import MessageStore

log = logging.getLogger("nyse_pillar_fix.client")

_B36 = string.digits + string.ascii_uppercase


def _base36(n: int) -> str:
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = _B36[r] + out
        if n == 0:
            return out


class ClOrdIDGenerator:
    """Generates ClOrdIDs that are unique across restarts within a trading day.

    Format: ``<prefix><base36 start-time><base36 counter>``, at most 20 characters.
    """

    def __init__(self, prefix: str = "") -> None:
        if len(prefix) > 6:
            raise ValueError("ClOrdID prefix is limited to 6 characters")
        self._stem = prefix + _base36(int(time.time() * 1000))
        self._counter = itertools.count(1)

    def __call__(self) -> str:
        cl = f"{self._stem}{_base36(next(self._counter)).rjust(4, '0')}"
        if len(cl) > 20:
            raise RuntimeError("ClOrdID space exhausted")
        return cl


@dataclass
class OrderState:
    """Client-side view of an order, updated from Execution Reports."""

    request: _OrderFields
    cl_ord_id: str
    order_id: str | None = None
    ord_status: OrdStatus | str | None = None
    cum_qty: int = 0
    leaves_qty: int | None = None
    reports: list[ExecutionReport] = field(default_factory=list, repr=False)
    cancel_rejects: list[OrderCancelReject] = field(default_factory=list, repr=False)
    #: Live (not busted) fills by ExecID: (LastQty, LastPx). Busts and corrections
    #: reference the fill they change through ExecRefID.
    fills: dict[str, tuple[int, Decimal]] = field(default_factory=dict, repr=False)
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    @property
    def notional(self) -> Decimal:
        """Filled notional of the live fills."""
        return sum((px * qty for qty, px in self.fills.values()), Decimal(0))

    @property
    def avg_px(self) -> Decimal | None:
        """Average price of the live fills, or ``None`` when nothing is filled."""
        qty = sum(q for q, _ in self.fills.values())
        return self.notional / qty if qty > 0 else None

    def apply_fill_accounting(self, report: ExecutionReport) -> None:
        """Update live fills and CumQty from one Execution Report.

        * a new fill (ExecTransType 0, ExecType 1 or 2) is recorded under its ExecID,
          unless that ExecID is already recorded (a duplicate report);
        * a trade bust (ExecTransType 1) removes the fill named by ExecRefID;
        * a trade correction (ExecTransType 2) replaces the fill named by ExecRefID with
          the corrected LastQty/LastPx under the correction's ExecID.
        """
        trans = report.exec_trans_type
        if trans in (ExecTransType.NEW, None):
            if (
                report.exec_type in (ExecType.PARTIAL_FILL, ExecType.FILL)
                and report.last_qty is not None
                and report.last_qty > 0
                and report.last_px is not None
                and report.exec_id is not None
                and report.exec_id not in self.fills
            ):
                self.fills[report.exec_id] = (report.last_qty, report.last_px)
        elif trans == ExecTransType.CANCEL:
            self.fills.pop(report.exec_ref_id or "", None)
        elif trans == ExecTransType.CORRECT:
            ref = report.exec_ref_id or ""
            if (
                ref in self.fills
                and report.exec_id is not None
                and (report.exec_id == ref or report.exec_id not in self.fills)
                and report.last_qty is not None
                and report.last_px is not None
            ):
                del self.fills[ref]
                self.fills[report.exec_id] = (report.last_qty, report.last_px)
        if report.cum_qty is not None:
            self.cum_qty = report.cum_qty

    @property
    def symbol(self) -> str:
        return self.request.symbol

    @property
    def side(self) -> Side | str:
        return self.request.side

    @property
    def routing(self) -> Routing:
        return self.request.routing

    @property
    def is_terminal(self) -> bool:
        return self.ord_status in TERMINAL_ORD_STATUSES

    async def wait_for(self, predicate: Callable[[OrderState], bool], timeout: float | None = None) -> OrderState:
        async with self._cond:
            await asyncio.wait_for(self._cond.wait_for(lambda: predicate(self)), timeout)
        return self

    async def wait_status(self, *statuses: OrdStatus | str, timeout: float | None = None) -> OrderState:
        wanted = {str(getattr(s, "value", s)) for s in statuses}
        return await self.wait_for(lambda o: str(getattr(o.ord_status, "value", o.ord_status)) in wanted, timeout)

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()


class PillarClient:
    """Order-entry client.

    Example::

        config = SessionConfig(sender_comp_id="ABC1", target_comp_id=Market.ARCA,
                               username="ABC1", password="secret",
                               endpoints=[("10.0.0.1", 6000), ("10.0.0.2", 6000)])
        async with PillarClient(config, mpid="ABCD") as client:
            order = await client.new_order("IBM", Side.BUY, 100, price=Decimal("150.25"))
            await order.wait_status(OrdStatus.NEW, timeout=5)
            await client.cancel(order)
    """

    def __init__(
        self,
        config: SessionConfig,
        *,
        store: MessageStore | None = None,
        mpid: str | None = None,
        sub_id: str | None = None,
        mmid: str | None = None,
        cl_ord_id_prefix: str = "",
        on_execution_report: Callable[[ExecutionReport], Awaitable[None] | None] | None = None,
        on_cancel_reject: Callable[[OrderCancelReject], Awaitable[None] | None] | None = None,
        on_session_reject: Callable[[Message], Awaitable[None] | None] | None = None,
        on_disconnect: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        self.session = FixSession(
            config,
            store,
            on_app_message=self._on_app_message,
            on_session_reject=on_session_reject,
            on_disconnect=on_disconnect,
        )
        self.default_routing = Routing(mpid=mpid, sub_id=sub_id, mmid=mmid)
        self.next_cl_ord_id = ClOrdIDGenerator(cl_ord_id_prefix)
        self.on_execution_report = on_execution_report
        self.on_cancel_reject = on_cancel_reject
        #: Orders by every ClOrdID they have carried (original and replacements).
        self.orders: dict[str, OrderState] = {}
        #: ClOrdID of each in-flight cancel/replace request -> the order it targets.
        self._pending: dict[str, OrderState] = {}
        #: ClOrdID of each in-flight cancel/replace -> the replacement request.
        self._pending_replace: dict[str, OrderCancelReplaceRequest] = {}

    # -- lifecycle -------------------------------------------------------------
    async def connect(self) -> Message:
        return await self.session.connect()

    async def logout(self, text: str | None = None) -> None:
        await self.session.logout(text)

    async def __aenter__(self) -> PillarClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.logout()

    # -- order entry -------------------------------------------------------------
    def _routing(self, routing: Routing | None) -> Routing:
        return dataclasses.replace(routing or self.default_routing)

    async def new_order(
        self,
        symbol: str,
        side: Side | str,
        order_qty: int,
        price: Decimal | str | float | None = None,
        *,
        ord_type: OrdType | str | None = None,
        time_in_force: TimeInForce | str = TimeInForce.DAY,
        routing: Routing | None = None,
        cl_ord_id: str | None = None,
        **fields: Any,
    ) -> OrderState:
        """Send a New Order - Single (35=D). Extra keyword arguments map to :class:`NewOrderSingle` fields."""
        if ord_type is None:
            ord_type = OrdType.MARKET if price is None else OrdType.LIMIT
        req = NewOrderSingle(
            cl_ord_id=cl_ord_id or self.next_cl_ord_id(),
            symbol=symbol,
            side=side,
            order_qty=order_qty,
            price=Decimal(str(price)) if price is not None else None,
            ord_type=ord_type,
            time_in_force=time_in_force,
            routing=self._routing(routing),
            **fields,
        )
        return await self.send_new_order(req)

    async def send_new_order(self, req: NewOrderSingle) -> OrderState:
        if req.cl_ord_id in self.orders:
            raise ValueError(f"duplicate ClOrdID {req.cl_ord_id}")
        state = OrderState(request=req, cl_ord_id=req.cl_ord_id)
        self.orders[req.cl_ord_id] = state
        try:
            await self.session.send(req.to_message())
        except BaseException:
            del self.orders[req.cl_ord_id]
            raise
        return state

    def _resolve(self, order: OrderState | str) -> OrderState:
        if isinstance(order, OrderState):
            return order
        try:
            return self.orders[order]
        except KeyError:
            raise KeyError(f"unknown ClOrdID {order!r}") from None

    async def cancel(self, order: OrderState | str, *, cl_ord_id: str | None = None) -> str:
        """Cancel a single order. Side, Symbol and routing header are copied from the order."""
        state = self._resolve(order)
        req = OrderCancelRequest(
            cl_ord_id=cl_ord_id or self.next_cl_ord_id(),
            orig_cl_ord_id=state.cl_ord_id,
            side=state.side,
            symbol=state.symbol,
            symbol_sfx=state.request.symbol_sfx,
            routing=dataclasses.replace(state.routing, retail=False),
        )
        self._pending[req.cl_ord_id] = state
        await self.session.send(req.to_message())
        return req.cl_ord_id

    async def bulk_cancel(
        self,
        code: BulkCancelCode | str,
        *,
        side: Side | str | None = None,
        symbol: str | None = None,
        routing: Routing | None = None,
        cl_ord_id: str | None = None,
    ) -> str:
        """Bulk cancel / block / unblock using a Bulk Cancel Code in OrderID (37)."""
        req = OrderCancelRequest(
            cl_ord_id=cl_ord_id or self.next_cl_ord_id(),
            bulk_cancel_code=code,
            side=side,
            symbol=symbol,
            routing=self._routing(routing),
        )
        await self.session.send(req.to_message())
        return req.cl_ord_id

    async def replace(self, order: OrderState | str, *, cl_ord_id: str | None = None, **changes: Any) -> str:
        """Cancel/Replace (35=G): resend the order with *changes* applied.

        Reducing quantity, or changing side between Sell / Sell Short / Sell Short Exempt,
        is treated by Pillar as a Modify that keeps priority; any other change loses it.
        """
        state = self._resolve(order)
        base = {
            f.name: getattr(state.request, f.name)
            for f in dataclasses.fields(state.request)
            if f.init and f.name not in ("cl_ord_id", "orig_cl_ord_id", "settlement_type")
        }
        base.update(changes)
        if "price" in changes and changes["price"] is not None:
            base["price"] = Decimal(str(changes["price"]))
        req = OrderCancelReplaceRequest(
            cl_ord_id=cl_ord_id or self.next_cl_ord_id(), orig_cl_ord_id=state.cl_ord_id, **base
        )
        self._pending[req.cl_ord_id] = state
        self._pending_replace[req.cl_ord_id] = req
        await self.session.send(req.to_message())
        return req.cl_ord_id

    # -- inbound -----------------------------------------------------------------
    async def _on_app_message(self, msg: Message) -> None:
        if msg.msg_type == MsgType.EXECUTION_REPORT:
            report = ExecutionReport.from_message(msg)
            state = self._apply_report(report)
            if state is not None:
                await state._notify()
            if self.on_execution_report is not None:
                r = self.on_execution_report(report)
                if asyncio.iscoroutine(r):
                    await r
        elif msg.msg_type == MsgType.ORDER_CANCEL_REJECT:
            reject = OrderCancelReject.from_message(msg)
            self._pending_replace.pop(reject.cl_ord_id or "", None)
            state = self._pending.pop(reject.cl_ord_id or "", None) or self.orders.get(reject.orig_cl_ord_id or "")
            if state is not None:
                state.cancel_rejects.append(reject)
                if reject.ord_status is not None and reject.ord_status != OrdStatus.REJECTED:
                    state.ord_status = reject.ord_status
                await state._notify()
            if self.on_cancel_reject is not None:
                r = self.on_cancel_reject(reject)
                if asyncio.iscoroutine(r):
                    await r

    def _apply_report(self, report: ExecutionReport) -> OrderState | None:
        key = report.cl_ord_id or ""
        state = self.orders.get(key) or self._pending.get(key) or self.orders.get(report.orig_cl_ord_id or "")
        if state is None:
            log.debug("execution report for unknown order %s", key)
            return None
        state.reports.append(report)
        if report.order_id:
            state.order_id = report.order_id
        if report.exec_type == ExecType.REPLACED and key in self._pending:
            new_req = self._pending_replace.pop(key, None)
            if new_req is not None:
                state.request = new_req
            state.cl_ord_id = key
            self.orders[key] = state
            self._pending.pop(key, None)
        elif report.exec_type in (ExecType.CANCELED, ExecType.REJECTED) and key in self._pending:
            self._pending.pop(key, None)
            self._pending_replace.pop(key, None)
        state.apply_fill_accounting(report)
        if report.ord_status is not None and report.ord_status != OrdStatus.REPLACED:
            state.ord_status = report.ord_status
        elif report.ord_status == OrdStatus.REPLACED:
            state.ord_status = OrdStatus.NEW if not state.cum_qty else OrdStatus.PARTIALLY_FILLED
        if report.leaves_qty is not None:
            state.leaves_qty = report.leaves_qty
        return state

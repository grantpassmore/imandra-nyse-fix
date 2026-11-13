"""Typed application messages: order-entry requests and gateway responses."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from . import tags as T
from .codec import Message, parse_utc_timestamp
from .enums import (
    AttributedQuote,
    AuctionEligible,
    BulkCancelCode,
    CancelInsteadOfReprice,
    CxlRejResponseTo,
    ExecInst,
    ExecTransType,
    ExecType,
    ExtendedExecInst,
    InterestType,
    MsgType,
    OrderCapacity,
    OrdStatus,
    OrdType,
    ProactiveIfLocked,
    RoutingInst,
    RoutingStrategy,
    SelfTradeType,
    SettlementType,
    Side,
    SpecialOrdType,
    SubIDIndicator,
    TimeInForce,
    TradingSessionID,
)


def _tag(tag: int, default: Any = None, conv: Any = None) -> Any:
    return field(default=default, metadata={"tag": tag, "conv": conv})


def _emit(obj: Any, msg: Message) -> None:
    for f in dataclasses.fields(obj):
        tag = f.metadata.get("tag")
        if tag is None:
            continue
        value = getattr(obj, f.name)
        if value is None:
            continue
        if tag == T.TradingSessionID:
            msg.append(T.NoTradingSessions, 1)
        msg.append(tag, value)


@dataclass
class Routing:
    """Header fields that identify the firm, desk and destination of an order.

    Cancel and Cancel/Replace requests must repeat the values used on the original order.
    """

    mpid: str | None = None  # OnBehalfOfCompID (115) - required on application messages
    sub_id: str | None = None  # OnBehalfOfSubID (116)
    mmid: str | None = None  # SenderSubID (50) - Market Maker ID
    deliver_to: str | None = None  # DeliverToCompID (128) - Algo MPID / NYSE Texas IB firm
    retail: bool = False  # TargetSubID (57) = "RET"

    def apply(self, msg: Message) -> None:
        msg.set(T.OnBehalfOfCompID, self.mpid)
        msg.set(T.OnBehalfOfSubID, self.sub_id)
        msg.set(T.SenderSubID, self.mmid)
        msg.set(T.DeliverToCompID, self.deliver_to)
        msg.set(T.TargetSubID, "RET" if self.retail else None)

    @classmethod
    def from_message(cls, msg: Message) -> Routing:
        return cls(
            mpid=msg.get(T.OnBehalfOfCompID),
            sub_id=msg.get(T.OnBehalfOfSubID),
            mmid=msg.get(T.SenderSubID),
            deliver_to=msg.get(T.DeliverToCompID),
            retail=msg.get(T.TargetSubID) == "RET",
        )


@dataclass(kw_only=True)
class _OrderFields:
    """Fields shared by New Order Single (D) and Cancel/Replace (G), in wire order."""

    account: str | None = _tag(T.Account)
    cl_ord_id: str = _tag(T.ClOrdID)
    exec_inst: ExecInst | str | None = _tag(T.ExecInst)
    order_qty: int = _tag(T.OrderQty)
    ord_type: OrdType | str = _tag(T.OrdType, OrdType.LIMIT)
    price: Decimal | str | None = _tag(T.Price)
    side: Side | str = _tag(T.Side)
    symbol: str = _tag(T.Symbol)
    text: str | None = _tag(T.Text)
    time_in_force: TimeInForce | str = _tag(T.TimeInForce, TimeInForce.DAY)
    transact_time: datetime | None = _tag(T.TransactTime)
    settlement_type: SettlementType | str | None = _tag(T.SettlementType)
    symbol_sfx: str | None = _tag(T.SymbolSfx)
    client_id: str | None = _tag(T.ClientID)
    min_qty: int | None = _tag(T.MinQty)
    max_floor: int | None = _tag(T.MaxFloor)
    locate_reqd: bool | None = _tag(T.LocateReqd)
    expire_time: str | None = _tag(T.ExpireTime)  # YYYYMMDD-HH:MM:SS
    effective_time: str | None = _tag(T.EffectiveTime)  # YYYYMMDD-HH:MM:SS
    trading_session_id: TradingSessionID | str = _tag(T.TradingSessionID, TradingSessionID.CORE)
    order_capacity: OrderCapacity | str = _tag(T.OrderCapacity, OrderCapacity.AGENCY)
    participation_rate: Decimal | str | None = _tag(T.ParticipationRate)
    locate_broker: str | None = _tag(T.LocateBroker)
    self_trade_type: SelfTradeType | str | None = _tag(T.SelfTradeType)
    special_ord_type: SpecialOrdType | str | None = _tag(T.SpecialOrdType)
    routing_inst: RoutingInst | str | None = _tag(T.RoutingInst)
    offset_price: Decimal | str | None = _tag(T.OffsetPrice)
    extended_exec_inst: ExtendedExecInst | str | None = _tag(T.ExtendedExecInst)
    introducing_badge_id: str | None = _tag(T.IntroducingBadgeID)
    parent_firm_cl_ord_id: str | None = _tag(T.ParentFirmClOrdID)
    parent_firm_mpid: str | None = _tag(T.ParentFirmMPID)
    interest_type: InterestType | str | None = _tag(T.InterestType)
    attributed_quote: AttributedQuote | str | None = _tag(T.AttributedQuote)
    proactive_if_locked: ProactiveIfLocked | str | None = _tag(T.ProactiveIfLocked)
    cancel_instead_of_reprice: CancelInsteadOfReprice | str | None = _tag(T.CancelInsteadOfReprice)
    route_to_broker: bool | None = _tag(T.RouteToBroker)
    broker_oms_id: str | None = _tag(T.BrokerOMSID)
    sub_id_indicator: SubIDIndicator | str | None = _tag(T.SubIDIndicator)
    target_strategy: str | None = _tag(T.TargetStrategy)
    eligible_10b18: bool | None = _tag(T.Eligible10b18)
    auction_eligible: AuctionEligible | str | None = _tag(T.AuctionEligible)
    min_participation_rate: Decimal | str | None = _tag(T.MinParticipationRate)
    max_participation_rate: Decimal | str | None = _tag(T.MaxParticipationRate)
    would_price: Decimal | str | None = _tag(T.WouldPrice)
    routing_strategy: RoutingStrategy | str | None = _tag(T.RoutingStrategy)

    routing: Routing = field(default_factory=Routing)

    MSG_TYPE: ClassVar[str] = ""

    def __post_init__(self) -> None:
        if isinstance(self.price, (int, float)):
            self.price = Decimal(str(self.price))
        # Short sales must carry LocateReqd=N (section 8.1).
        from .enums import SHORT_SIDES

        if self.locate_reqd is None and self.side in SHORT_SIDES:
            self.locate_reqd = False

    def to_message(self) -> Message:
        msg = Message(self.MSG_TYPE)
        self.routing.apply(msg)
        _emit(self, msg)
        # booleans with 0/1 wire values
        if self.eligible_10b18 is not None:
            msg.set(T.Eligible10b18, "1" if self.eligible_10b18 else "0")
        return msg


@dataclass(kw_only=True)
class NewOrderSingle(_OrderFields):
    MSG_TYPE: ClassVar[str] = MsgType.NEW_ORDER_SINGLE.value


@dataclass(kw_only=True)
class OrderCancelReplaceRequest(_OrderFields):
    orig_cl_ord_id: str = _tag(T.OrigClOrdID)
    MSG_TYPE: ClassVar[str] = MsgType.ORDER_CANCEL_REPLACE_REQUEST.value

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.settlement_type is not None:
            raise ValueError("SettlementType (63) is not defined on Cancel/Replace requests")


@dataclass(kw_only=True)
class OrderCancelRequest:
    """Single order cancel (OrigClOrdID + Side + Symbol) or bulk cancel (OrderID = code)."""

    cl_ord_id: str = _tag(T.ClOrdID)
    bulk_cancel_code: BulkCancelCode | str | None = _tag(T.OrderID)
    orig_cl_ord_id: str | None = _tag(T.OrigClOrdID)
    side: Side | str | None = _tag(T.Side)
    symbol: str | None = _tag(T.Symbol)
    transact_time: datetime | None = _tag(T.TransactTime)
    symbol_sfx: str | None = _tag(T.SymbolSfx)
    route_to_broker: bool | None = _tag(T.RouteToBroker)
    routing: Routing = field(default_factory=Routing)

    MSG_TYPE: ClassVar[str] = MsgType.ORDER_CANCEL_REQUEST.value

    def to_message(self) -> Message:
        msg = Message(self.MSG_TYPE)
        self.routing.apply(msg)
        _emit(self, msg)
        return msg


# --- responses -----------------------------------------------------------------


def _dec(v: str) -> Decimal:
    return Decimal(v)


def _ts(v: str) -> datetime:
    return parse_utc_timestamp(v)


def _enum(cls: Any):
    def conv(v: str) -> Any:
        try:
            return cls(v)
        except ValueError:
            return v

    return conv


def _parse(cls: Any, msg: Message) -> Any:
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        tag = f.metadata.get("tag")
        if tag is None:
            continue
        raw = msg.get(tag)
        if raw is None:
            continue
        conv = f.metadata.get("conv")
        try:
            kwargs[f.name] = conv(raw) if conv else raw
        except (ValueError, ArithmeticError):
            kwargs[f.name] = raw
    return cls(raw=msg, routing=Routing.from_message(msg), **kwargs)


@dataclass
class ExecutionReport:
    """Execution Report (35=8): acks, fills, cancels, replaces, rejects, busts, priority updates."""

    raw: Message = field(repr=False)
    routing: Routing = field(repr=False)
    cl_ord_id: str | None = _tag(T.ClOrdID)
    orig_cl_ord_id: str | None = _tag(T.OrigClOrdID)
    order_id: str | None = _tag(T.OrderID)
    exec_id: str | None = _tag(T.ExecID)
    exec_ref_id: str | None = _tag(T.ExecRefID)
    exec_trans_type: ExecTransType | str | None = _tag(T.ExecTransType, conv=_enum(ExecTransType))
    exec_type: ExecType | str | None = _tag(T.ExecType, conv=_enum(ExecType))
    ord_status: OrdStatus | str | None = _tag(T.OrdStatus, conv=_enum(OrdStatus))
    symbol: str | None = _tag(T.Symbol)
    symbol_sfx: str | None = _tag(T.SymbolSfx)
    side: Side | str | None = _tag(T.Side, conv=_enum(Side))
    ord_type: OrdType | str | None = _tag(T.OrdType, conv=_enum(OrdType))
    order_qty: int | None = _tag(T.OrderQty, conv=int)
    price: Decimal | None = _tag(T.Price, conv=_dec)
    time_in_force: TimeInForce | str | None = _tag(T.TimeInForce, conv=_enum(TimeInForce))
    cum_qty: int | None = _tag(T.CumQty, conv=int)
    leaves_qty: int | None = _tag(T.LeavesQty, conv=int)
    last_qty: int | None = _tag(T.LastQty, conv=int)
    last_px: Decimal | None = _tag(T.LastPx, conv=_dec)
    last_mkt: str | None = _tag(T.LastMkt)
    deal_id: str | None = _tag(T.DealID)
    ref_deal_id: str | None = _tag(T.RefDealID)
    liquidity_indicator: str | None = _tag(T.LiquidityIndicator)
    working_price: Decimal | None = _tag(T.WorkingPrice, conv=_dec)
    working_away_from_display: str | None = _tag(T.WorkingAwayFromDisplay)
    unsolicited_ack_type: str | None = _tag(T.UnsolicitedAckType)
    participant_type: str | None = _tag(T.ParticipantType)
    flow_indicator: str | None = _tag(T.FlowIndicator)
    executed_trading_session: str | None = _tag(T.ExecutedTradingSession)
    text: str | None = _tag(T.Text)
    account: str | None = _tag(T.Account)
    transact_time: datetime | None = _tag(T.TransactTime, conv=_ts)
    nanosecond_transact_time: str | None = _tag(T.NanosecondTransactTime)
    nanosecond_sending_time: str | None = _tag(T.NanosecondSendingTime)

    @classmethod
    def from_message(cls, msg: Message) -> ExecutionReport:
        if msg.msg_type != MsgType.EXECUTION_REPORT:
            raise ValueError(f"not an Execution Report: 35={msg.msg_type}")
        return _parse(cls, msg)

    @property
    def is_fill(self) -> bool:
        return self.exec_type in (ExecType.PARTIAL_FILL, ExecType.FILL) and self.exec_trans_type in (
            ExecTransType.NEW,
            None,
        )

    @property
    def is_bust(self) -> bool:
        return self.exec_trans_type == ExecTransType.CANCEL

    @property
    def is_correction(self) -> bool:
        return self.exec_trans_type == ExecTransType.CORRECT

    @property
    def is_priority_update(self) -> bool:
        return self.unsolicited_ack_type is not None

    @property
    def reason_code(self) -> str | None:
        """Pillar reason code from Text (58), formatted ``'Rxxx: Description'``."""
        return parse_reason_code(self.text)[0]


@dataclass
class OrderCancelReject:
    """Order Cancel Reject (35=9)."""

    raw: Message = field(repr=False)
    routing: Routing = field(repr=False)
    cl_ord_id: str | None = _tag(T.ClOrdID)
    orig_cl_ord_id: str | None = _tag(T.OrigClOrdID)
    order_id: str | None = _tag(T.OrderID)
    ord_status: OrdStatus | str | None = _tag(T.OrdStatus, conv=_enum(OrdStatus))
    cxl_rej_response_to: CxlRejResponseTo | str | None = _tag(T.CxlRejResponseTo, conv=_enum(CxlRejResponseTo))
    text: str | None = _tag(T.Text)
    transact_time: datetime | None = _tag(T.TransactTime, conv=_ts)
    nanosecond_transact_time: str | None = _tag(T.NanosecondTransactTime)

    @classmethod
    def from_message(cls, msg: Message) -> OrderCancelReject:
        if msg.msg_type != MsgType.ORDER_CANCEL_REJECT:
            raise ValueError(f"not an Order Cancel Reject: 35={msg.msg_type}")
        return _parse(cls, msg)

    @property
    def reason_code(self) -> str | None:
        return parse_reason_code(self.text)[0]


def parse_reason_code(text: str | None) -> tuple[str | None, str | None]:
    """Split Pillar ``Text`` (58) of the form ``'Rxxx: Description'`` into ``(code, description)``."""
    if not text:
        return None, None
    head, sep, tail = text.partition(":")
    head = head.strip()
    if sep and head[:1] in ("R", "r") and head[1:].isdigit():
        return head[1:], tail.strip()
    if sep and head.isdigit():
        return head, tail.strip()
    return None, text


def parse_application_message(msg: Message) -> ExecutionReport | OrderCancelReject | Message:
    if msg.msg_type == MsgType.EXECUTION_REPORT:
        return ExecutionReport.from_message(msg)
    if msg.msg_type == MsgType.ORDER_CANCEL_REJECT:
        return OrderCancelReject.from_message(msg)
    return msg

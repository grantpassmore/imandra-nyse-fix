"""Machine-readable transcription of the Pillar FIX message formats (spec v6.0).

Each :class:`FieldSpec` records the data type and systemic length limit from the
"Data Type" column, and, for enumerated fields, which values each market accepts
(the NYSE / American / National / Arca / Texas columns of the order-entry tables).
:data:`MESSAGES` records, per message type, which tags are defined and whether
each is Required (``Y``), Conditional (``C``) or Optional (``N``).

The gateway rejects messages carrying tags not defined for the message type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import tags as T
from .enums import ALL_MARKETS, Market

NYSE, AMER, NAT, ARCA, TX = Market.NYSE, Market.AMERICAN, Market.NATIONAL, Market.ARCA, Market.TEXAS
ALL = ALL_MARKETS
NOT_NYSE = frozenset({AMER, NAT, ARCA, TX})
ONLY_NYSE = frozenset({NYSE})
ONLY_TX = frozenset({TX})
ONLY_ARCA = frozenset({ARCA})
ONLY_NAT = frozenset({NAT})

#: Characters forbidden in customer-defined free-form strings (in addition to non-printables).
FORBIDDEN_CHARS = frozenset(",;|@<>&'\"")


@dataclass(frozen=True)
class FieldSpec:
    tag: int
    name: str
    type: str  # String, Char, Int, Qty, Price, Boolean, UTCTimestamp
    length: int | None = None
    #: Allowed values -> markets that accept them. ``None`` means not enumerated.
    values: dict[str, frozenset[Market]] | None = None
    #: Markets on which the field may be sent at all.
    markets: frozenset[Market] = ALL
    #: Customer-defined string restricted to printable ASCII minus FORBIDDEN_CHARS.
    restricted_chars: bool = False

    def allowed_values(self, market: Market | None = None) -> set[str] | None:
        if self.values is None:
            return None
        if market is None:
            return set(self.values)
        return {v for v, mk in self.values.items() if market in mk}


def _f(tag: int, type_: str, length: int | None = None, **kw) -> FieldSpec:
    return FieldSpec(tag, T.NAMES[tag], type_, length, **kw)


_SIDES = {"1": ALL, "2": ALL, "5": ALL, "6": ALL, "8": ONLY_TX, "9": ONLY_TX, "A": ONLY_TX}

FIELDS: dict[int, FieldSpec] = {
    f.tag: f
    for f in [
        # header
        _f(T.MsgSeqNum, "Int", 20),
        _f(T.PossDupFlag, "Boolean"),
        _f(T.SenderCompID, "String", 32),
        _f(T.SenderSubID, "String", 32),
        _f(T.SendingTime, "UTCTimestamp", 27),
        _f(T.TargetCompID, "String", 32),
        _f(T.TargetSubID, "String", 32),
        _f(T.PossResend, "Boolean"),
        _f(T.OnBehalfOfCompID, "String", 4),
        _f(T.OnBehalfOfSubID, "String", 4, restricted_chars=True),
        _f(T.OrigSendingTime, "UTCTimestamp", 27),
        _f(T.DeliverToCompID, "String", 5),
        # session
        _f(T.BeginSeqNo, "Int", 20),
        _f(T.EndSeqNo, "Int", 20),
        _f(T.NewSeqNo, "Int", 20),
        _f(T.RefSeqNum, "Int", 20),
        _f(T.RawDataLength, "Int", 1, values={"3": ALL}),
        _f(T.RawData, "String", 3),
        _f(T.EncryptMethod, "Int", 1, values={"0": ALL}),
        _f(T.HeartBtInt, "Int", 2),
        _f(T.TestReqID, "String", 20),
        _f(T.GapFillFlag, "Boolean"),
        _f(T.ResetSeqNumFlag, "Boolean", values={"N": ALL}),
        _f(T.RefTagID, "Int", 9),
        _f(T.RefMsgType, "String", 2),
        _f(T.SessionRejectReason, "Int", 2),
        _f(T.Username, "String", 16),
        _f(T.Password, "String", 32),
        _f(T.NextExpectedMsgSeqNum, "Int", 20),
        _f(T.SessionStatus, "Int", 1),
        # application
        _f(T.Account, "String", 16, restricted_chars=True),
        _f(T.ClOrdID, "String", 20, restricted_chars=True),
        _f(T.CumQty, "Qty", 9),
        _f(T.ExecID, "String", 32),
        _f(
            T.ExecInst,
            "Char",
            1,
            values={
                "d": NOT_NYSE,
                "f": ALL,
                "R": NOT_NYSE,
                "P": NOT_NYSE,
                "M": ALL,
                "N": ALL,
                "y": ALL,
                "L": ONLY_NYSE,
            },
        ),
        _f(T.ExecRefID, "String", 32),
        _f(T.ExecTransType, "Char", 1),
        _f(T.LastMkt, "String", 4),
        _f(T.LastPx, "Price", 16),
        _f(T.LastQty, "Qty", 9),
        _f(T.OrderID, "String", 20),
        _f(T.OrderQty, "Qty", 9),
        _f(T.OrdStatus, "Char", 1),
        _f(T.OrdType, "Char", 1, values={"1": ALL, "2": ALL, "7": ALL, "P": ALL}),
        _f(T.OrigClOrdID, "String", 20, restricted_chars=True),
        _f(T.Price, "Price", 16),
        _f(T.Side, "Char", 1, values=_SIDES),
        _f(T.Symbol, "String", 16),
        _f(T.Text, "String", 80, restricted_chars=True),
        _f(T.TimeInForce, "Char", 1, values={"0": ALL, "2": ALL, "3": ALL, "7": ALL}),
        _f(T.TransactTime, "UTCTimestamp", 27),
        _f(T.SettlementType, "Char", 1, values={"0": ONLY_TX, "1": ONLY_TX}, markets=ONLY_TX),
        _f(T.SymbolSfx, "String", 10),
        _f(T.ClientID, "String", 4, markets=ONLY_TX),
        _f(T.MinQty, "Qty", 5),
        _f(T.MaxFloor, "Qty", 5),
        _f(T.LocateReqd, "Boolean", values={"N": ALL}),
        _f(T.ExpireTime, "UTCTimestamp", 27, markets=ONLY_NYSE),
        _f(T.ExecType, "Char", 1),
        _f(T.LeavesQty, "Qty", 9),
        _f(T.EffectiveTime, "UTCTimestamp", 27, markets=ONLY_NYSE),
        _f(
            T.TradingSessionID,
            "Char",
            1,
            values={
                "0": ONLY_ARCA,
                "1": ALL,
                "2": ALL,
                "3": NOT_NYSE,
                "4": ALL,
                "5": ALL,
                "6": ALL,
                "7": ONLY_ARCA,
            },
        ),
        _f(T.NoTradingSessions, "Int", 1, values={"1": ALL}),
        _f(T.CxlRejResponseTo, "Char", 1),
        _f(T.OrderCapacity, "Char", 1, values={"A": ALL, "P": ALL, "R": ALL, "Q": ONLY_TX}),
        _f(T.ParticipationRate, "Price", 16, markets=ONLY_NYSE),
        _f(T.LocateBroker, "String", 4),
        _f(T.SelfTradeType, "Char", 1, values={v: ALL for v in "0TNOCD"}),
        _f(T.SpecialOrdType, "Char", 1, values={"T": ONLY_TX}),
        _f(
            T.RoutingInst,
            "Char",
            1,
            values={
                "N": ALL,
                "R": ALL,
                "D": NOT_NYSE,
                "S": NOT_NYSE,
                "1": NOT_NYSE,
                "2": NOT_NYSE,
                "3": NOT_NYSE,
                "8": ALL,
                "A": ALL,
                "E": ONLY_NYSE,
            },
        ),
        _f(T.OffsetPrice, "Price", 16),
        _f(
            T.ExtendedExecInst,
            "Char",
            1,
            values={
                "A": ALL,
                "2": ALL,
                "4": ONLY_NYSE,
                "5": ONLY_NAT,
                "7": frozenset({NYSE, NAT}),
                "8": frozenset({NYSE, AMER, ARCA, TX}),
                "9": frozenset({AMER, ARCA}),
                "D": frozenset({AMER}),
                "I": ONLY_NYSE,
            },
        ),
        _f(T.IntroducingBadgeID, "String", 4, markets=ONLY_TX),
        _f(T.BillTo, "String", 4),
        _f(T.ParentFirmClOrdID, "String", 20, markets=ONLY_TX),
        _f(T.ParentFirmMPID, "String", 4, markets=ONLY_TX),
        _f(T.InterestType, "Char", 1, values={"Q": NOT_NYSE}),
        _f(T.DealID, "String", 20),
        _f(T.DOrderAuctionPrice, "Price", 16),
        _f(T.LiquidityIndicator, "String"),
        _f(T.AttributedQuote, "Char", 1, values={"0": ALL, "1": NOT_NYSE}),
        _f(T.ProactiveIfLocked, "Char", 1, values={"0": ALL, "1": ALL, "2": ALL}),
        _f(T.CancelInsteadOfReprice, "Char", 1, values={"0": ALL, "1": NOT_NYSE, "3": ALL}),
        _f(T.WorkingPrice, "Price", 16),
        _f(T.FlowIndicator, "Char", 1),
        _f(T.WorkingAwayFromDisplay, "Char", 1),
        _f(T.UnsolicitedAckType, "Char", 1),
        _f(T.ParticipantType, "Char", 1),
        _f(T.NanosecondSendingTime, "String", 27),
        _f(T.NanosecondTransactTime, "String", 27),
        _f(T.RouteToBroker, "Char", 1, values={"Y": ONLY_TX, "N": ONLY_TX}, markets=ONLY_TX),
        _f(T.BrokerOMSID, "String", 4, markets=ONLY_TX),
        _f(T.SubIDIndicator, "Char", 1, values={"0": ALL, "1": ALL}),
        _f(T.AllocationFirmMPID, "String", 4),
        _f(T.AllocationFirmIntroducingBadgeID, "String", 4),
        _f(T.CATIMID, "String", 4),
        _f(T.TargetStrategy, "String", 20, markets=ONLY_NYSE, restricted_chars=True),
        _f(T.Eligible10b18, "Char", 1, values={"0": ONLY_NYSE, "1": ONLY_NYSE}, markets=ONLY_NYSE),
        _f(T.AuctionEligible, "Char", 1, values={v: ONLY_NYSE for v in "0123"}, markets=ONLY_NYSE),
        _f(T.MinParticipationRate, "Price", 16, markets=ONLY_NYSE),
        _f(T.MaxParticipationRate, "Price", 16, markets=ONLY_NYSE),
        _f(T.WouldPrice, "Price", 16, markets=ONLY_NYSE),
        _f(T.RoutingStrategy, "Int", 8, values={"1": ALL, "2": ONLY_NAT, "3": ONLY_NAT}),
        _f(T.ExecutedTradingSession, "Char", 1),
        _f(T.RefDealID, "String", 20),
        _f(T.AllocationIndicator, "Char", 1),
        _f(T.OriginalDealID, "String", 20),
        _f(T.RefExecTimestamp, "UTCTimestamp", 27),
    ]
}

#: Text (58) is 80 chars on inbound order messages but 40 on exchange responses.
TEXT_LENGTH_BY_MSG_TYPE = {"D": 80, "G": 80, "8": 40, "9": 40, "A": 100, "5": 100, "3": 100}


@dataclass(frozen=True)
class MessageSpec:
    msg_type: str
    name: str
    #: body tag -> 'Y' (required), 'C' (conditional) or 'N' (optional)
    body: dict[int, str]
    #: direction(s): 'in' = firm -> gateway, 'out' = gateway -> firm
    directions: frozenset[str] = field(default_factory=lambda: frozenset({"in", "out"}))
    application: bool = False

    @property
    def required(self) -> set[int]:
        return {t for t, r in self.body.items() if r == "Y"}


# Header tags a firm may send. 8, 9, 35, 10 are handled by the codec.
HEADER_IN: dict[int, str] = {
    T.MsgSeqNum: "Y",
    T.PossDupFlag: "C",
    T.SenderCompID: "Y",
    T.SenderSubID: "C",
    T.SendingTime: "Y",
    T.TargetCompID: "Y",
    T.TargetSubID: "C",
    T.PossResend: "C",
    T.OnBehalfOfCompID: "C",  # required on application messages
    T.OnBehalfOfSubID: "N",
    T.OrigSendingTime: "N",
    T.DeliverToCompID: "C",
}

_ORDER_COMMON = {
    T.Account: "N",
    T.ClOrdID: "Y",
    T.ExecInst: "C",
    T.OrderQty: "Y",
    T.OrdType: "Y",
    T.Price: "C",
    T.Side: "Y",
    T.Symbol: "Y",
    T.Text: "N",
    T.TimeInForce: "Y",
    T.TransactTime: "N",
    T.SymbolSfx: "N",
    T.ClientID: "C",
    T.MinQty: "N",
    T.MaxFloor: "C",
    T.LocateReqd: "C",
    T.ExpireTime: "N",
    T.EffectiveTime: "N",
    T.NoTradingSessions: "Y",
    T.TradingSessionID: "Y",
    T.OrderCapacity: "Y",
    T.ParticipationRate: "N",
    T.LocateBroker: "C",
    T.SelfTradeType: "N",
    T.SpecialOrdType: "C",
    T.RoutingInst: "C",
    T.OffsetPrice: "C",
    T.ExtendedExecInst: "C",
    T.IntroducingBadgeID: "C",
    T.ParentFirmClOrdID: "C",
    T.ParentFirmMPID: "C",
    T.InterestType: "C",
    T.AttributedQuote: "N",
    T.ProactiveIfLocked: "N",
    T.CancelInsteadOfReprice: "N",
    T.RouteToBroker: "C",
    T.BrokerOMSID: "C",
    T.SubIDIndicator: "N",
    T.TargetStrategy: "N",
    T.Eligible10b18: "N",
    T.AuctionEligible: "N",
    T.MinParticipationRate: "N",
    T.MaxParticipationRate: "N",
    T.WouldPrice: "N",
    T.RoutingStrategy: "N",
}

_IN = frozenset({"in"})
_OUT = frozenset({"out"})

MESSAGES: dict[str, MessageSpec] = {
    m.msg_type: m
    for m in [
        MessageSpec(
            "A",
            "Logon",
            {
                T.EncryptMethod: "Y",
                T.HeartBtInt: "Y",
                T.RawDataLength: "C",
                T.RawData: "C",
                T.ResetSeqNumFlag: "N",
                T.Username: "Y",
                T.Password: "C",  # required on request, omitted on response
                T.Text: "N",
                T.NextExpectedMsgSeqNum: "C",  # response only
                T.SessionStatus: "N",
            },
        ),
        MessageSpec("0", "Heartbeat", {T.TestReqID: "C"}),
        MessageSpec("1", "TestRequest", {T.TestReqID: "Y"}),
        MessageSpec("2", "ResendRequest", {T.BeginSeqNo: "Y", T.EndSeqNo: "Y"}),
        MessageSpec(
            "3",
            "Reject",
            {
                T.RefSeqNum: "Y",
                T.SessionRejectReason: "N",
                T.RefTagID: "N",
                T.RefMsgType: "N",
                T.Text: "N",
                T.NextExpectedMsgSeqNum: "Y",
            },
            directions=_OUT,
        ),
        MessageSpec("4", "SequenceReset", {T.GapFillFlag: "Y", T.NewSeqNo: "Y"}),
        MessageSpec("5", "Logout", {T.SessionStatus: "N", T.Text: "N", T.NextExpectedMsgSeqNum: "C"}),
        MessageSpec(
            "D",
            "NewOrderSingle",
            _ORDER_COMMON | {T.SettlementType: "N"},
            directions=_IN,
            application=True,
        ),
        MessageSpec(
            "F",
            "OrderCancelRequest",
            {
                T.ClOrdID: "Y",
                T.OrderID: "C",
                T.OrigClOrdID: "C",
                T.Side: "C",
                T.Symbol: "C",
                T.TransactTime: "N",
                T.SymbolSfx: "N",
                T.RouteToBroker: "C",
            },
            directions=_IN,
            application=True,
        ),
        MessageSpec(
            "G",
            "OrderCancelReplaceRequest",
            _ORDER_COMMON | {T.OrigClOrdID: "Y", T.SpecialOrdType: "N"},
            directions=_IN,
            application=True,
        ),
        MessageSpec(
            "9",
            "OrderCancelReject",
            {
                T.ClOrdID: "Y",
                T.OrderID: "Y",
                T.OrdStatus: "Y",
                T.OrigClOrdID: "C",
                T.Text: "N",
                T.TransactTime: "N",
                T.CxlRejResponseTo: "Y",
                T.NanosecondSendingTime: "Y",
                T.NanosecondTransactTime: "Y",
                T.RouteToBroker: "C",
            },
            directions=_OUT,
            application=True,
        ),
        MessageSpec(
            "8",
            "ExecutionReport",
            (
                _ORDER_COMMON
                | {
                    T.CumQty: "C",
                    T.ExecID: "Y",
                    T.ExecRefID: "C",
                    T.ExecTransType: "Y",
                    T.LastMkt: "C",
                    T.LastPx: "C",
                    T.LastQty: "C",
                    T.OrderID: "C",
                    T.OrdStatus: "Y",
                    T.OrigClOrdID: "C",
                    T.SettlementType: "N",
                    T.ExecType: "Y",
                    T.LeavesQty: "C",
                    T.DealID: "C",
                    T.LiquidityIndicator: "C",
                    T.WorkingPrice: "C",
                    T.FlowIndicator: "Y",
                    T.WorkingAwayFromDisplay: "C",
                    T.UnsolicitedAckType: "C",
                    T.ParticipantType: "C",
                    T.NanosecondSendingTime: "Y",
                    T.NanosecondTransactTime: "Y",
                    T.ExecutedTradingSession: "N",
                    T.RefDealID: "C",
                    # drop-copy-only tags
                    T.BillTo: "C",
                    T.DOrderAuctionPrice: "N",
                    T.AllocationFirmMPID: "C",
                    T.AllocationFirmIntroducingBadgeID: "C",
                    T.CATIMID: "N",
                    T.AllocationIndicator: "C",
                    T.OriginalDealID: "C",
                    T.RefExecTimestamp: "C",
                },
            ),
            directions=_OUT,
            application=True,
        ),
    ]
}

# --- Price & quantity limits (section 3) ---------------------------------------
MIN_PRICE = Decimal("0.000001")
MAX_PRICE = Decimal("999999999.999999")
MAX_PRICE_DECIMALS = 6
MAX_ORDER_QTY_FIELD = 999_999_999

#: Max limit price by XDP Price Scale Code (section 3).
MAX_PRICE_BY_SCALE_CODE = {6: Decimal("2147.48"), 4: Decimal("214748.364"), 3: Decimal("999999.999")}

STANDARD_MAX_ORDER_QTY = 5_000_000
AUCTION_OR_CROSS_MAX_ORDER_QTY = 25_000_000

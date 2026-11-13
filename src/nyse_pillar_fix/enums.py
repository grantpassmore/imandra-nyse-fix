"""Enumerated field values from the NYSE Pillar Gateway FIX Protocol Specification (v6.0).

Every enum is a ``str`` enum whose value is the exact FIX wire value, so members can be
passed anywhere a field value is expected.
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Market(_StrEnum):
    """NYSE Group equities markets, identified by Market Identifier Code (MIC).

    The MIC is the gateway's SenderCompID on outbound messages and the firm's
    TargetCompID (56) on inbound messages.
    """

    NYSE = "XNYS"
    AMERICAN = "XASE"
    NATIONAL = "XCIS"
    ARCA = "ARCX"
    TEXAS = "XCHI"


ALL_MARKETS: frozenset[Market] = frozenset(Market)


class MsgType(_StrEnum):
    LOGON = "A"
    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    SEQUENCE_RESET = "4"
    LOGOUT = "5"
    NEW_ORDER_SINGLE = "D"
    ORDER_CANCEL_REQUEST = "F"
    ORDER_CANCEL_REPLACE_REQUEST = "G"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"


SESSION_MSG_TYPES = frozenset(
    {
        MsgType.LOGON,
        MsgType.HEARTBEAT,
        MsgType.TEST_REQUEST,
        MsgType.RESEND_REQUEST,
        MsgType.REJECT,
        MsgType.SEQUENCE_RESET,
        MsgType.LOGOUT,
    }
)
APP_MSG_TYPES = frozenset(MsgType) - SESSION_MSG_TYPES


class Side(_StrEnum):
    BUY = "1"
    SELL = "2"
    SELL_SHORT = "5"
    SELL_SHORT_EXEMPT = "6"
    CROSS = "8"  # NYSE Texas only
    CROSS_SHORT = "9"  # NYSE Texas only
    CROSS_SHORT_EXEMPT = "A"  # NYSE Texas only


#: Sides that require LocateReqd (114) = N.
SHORT_SIDES = frozenset({Side.SELL_SHORT, Side.SELL_SHORT_EXEMPT, Side.CROSS_SHORT, Side.CROSS_SHORT_EXEMPT})
CROSS_SIDES = frozenset({Side.CROSS, Side.CROSS_SHORT, Side.CROSS_SHORT_EXEMPT})


class OrdType(_StrEnum):
    MARKET = "1"
    LIMIT = "2"
    INSIDE_LIMIT = "7"
    PEGGED = "P"


class TimeInForce(_StrEnum):
    DAY = "0"
    AT_THE_OPENING = "2"
    IOC = "3"
    AT_THE_CLOSE = "7"


class ExecInst(_StrEnum):
    TRACKING_ORDER = "d"
    ISO = "f"
    PRIMARY_PEG = "R"
    MARKET_PEG = "P"
    MPL = "M"  # Midpoint Passive Liquidity
    NON_DISPLAYED = "N"
    TRADE_AT_ISO = "y"
    LAST_SALE_PEG = "L"  # NYSE only
    STOCK_OPTION_CONTINGENT = "v"  # outbound drop copy only (NYSE Texas)


class TradingSessionID(_StrEnum):
    OVERNIGHT = "0"  # NYSE Arca only
    EARLY = "1"
    CORE = "2"
    LATE = "3"  # not NYSE
    EARLY_CORE = "4"
    CORE_LATE = "5"
    EARLY_CORE_LATE = "6"
    OVERNIGHT_EARLY_CORE_LATE = "7"  # NYSE Arca only


class OrderCapacity(_StrEnum):
    AGENCY = "A"
    PRINCIPAL = "P"
    RISKLESS_PRINCIPAL = "R"
    ERROR_ACCOUNT = "Q"  # NYSE Texas, Brokerplex only


class SelfTradeType(_StrEnum):
    SESSION_DEFAULT = "0"
    NONE = "T"
    CANCEL_NEWEST = "N"
    CANCEL_OLDEST = "O"
    CANCEL_BOTH = "C"
    CANCEL_DECREMENT = "D"


class SpecialOrdType(_StrEnum):
    DMM_OPEN_REOPEN_CLOSE = "1"  # drop copy only
    DMM_PRE_AUCTION = "2"  # drop copy only
    DMM_AFTER_AUCTION = "3"  # drop copy only
    NO_ROUTE_TO_IOI_DORDERS = "8"  # drop copy only
    QCT = "T"  # NYSE Texas


class RoutingInst(_StrEnum):
    NON_ROUTABLE = "N"
    ROUTABLE = "R"
    DIRECTED = "D"  # Primary Only
    DIRECTED_ROUTABLE = "S"  # PO+S
    PRIMARY_UNTIL_0945 = "1"
    PRIMARY_AFTER_1555 = "2"
    PRIMARY_UNTIL_0945_AND_AFTER_1555 = "3"
    MINIMUM_FILL = "8"
    ROUTE_TO_ATS = "A"
    ROUTE_TO_ALGO = "E"  # NYSE only


class ExtendedExecInst(_StrEnum):
    ADD_LIQUIDITY_ONLY = "A"
    NO_ROUTE_TO_IOI = "2"
    RETAIL_ORDER_TYPE_1 = "4"
    RETAIL_ORDER_TYPE_2 = "5"
    RETAIL_PROVIDER = "7"
    IMBALANCE_OFFSET = "8"
    DISCRETIONARY_PEG = "9"
    DARK_PRIMARY_PEG = "D"
    ISSUER_DIRECT_OFFERING = "I"


class InterestType(_StrEnum):
    FLOOR_BROKER_CROSS_72D = "3"  # drop copy
    Q_ORDER = "Q"


class AttributedQuote(_StrEnum):
    NOT_ATTRIBUTED = "0"
    ATTRIBUTED = "1"


class ProactiveIfLocked(_StrEnum):
    NONE = "0"
    PROACTIVE_IF_LOCKED = "1"
    PROACTIVE_NON_DISPLAY = "2"


class CancelInsteadOfReprice(_StrEnum):
    NOT_APPLICABLE = "0"
    LULD_ONLY = "1"
    ANY_REASON = "3"


class SubIDIndicator(_StrEnum):
    USE_SUB_ID = "0"
    IGNORE_SUB_ID = "1"


class AuctionEligible(_StrEnum):
    NONE = "0"
    CLOSING = "1"
    OPENING = "2"
    OPENING_AND_CLOSING = "3"


class RoutingStrategy(_StrEnum):
    MIDPOINT_PING = "1"
    RETAIL_PRICE_IMPROVEMENT_SEEKING = "2"  # NYSE National
    RETAIL_MIDPOINT_PING = "3"  # NYSE National


class SettlementType(_StrEnum):
    REGULAR = "0"
    CASH = "1"  # Cross orders only


class BulkCancelCode(_StrEnum):
    """Bulk cancel codes, sent in OrderID (37) on an Order Cancel Request."""

    SESSION_DAY_DIRECTED = "1"
    SESSION_ALL = "2"
    SESSION_OPENING_DAY_DIRECTED = "3"
    MPID_OPENING_DAY_DIRECTED = "4"
    MPID_DAY_DIRECTED = "5"
    MPID_OPENING_AND_CLOSE = "7"
    MPID_DAY = "8"
    MPID_DAY_DIRECTED_AND_BLOCK = "9"
    MPID_BLOCK = "10"
    MPID_UNBLOCK = "11"
    MPID_DIRECTED = "12"


class ExecTransType(_StrEnum):
    NEW = "0"
    CANCEL = "1"  # trade break
    CORRECT = "2"  # trade correction (NYSE Texas)


class ExecType(_StrEnum):
    NEW = "0"
    PARTIAL_FILL = "1"
    FILL = "2"
    DONE_FOR_DAY = "3"
    CANCELED = "4"
    REPLACED = "5"
    PENDING_CANCEL = "6"
    REJECTED = "8"
    BILLABLE_CANCEL = "C"
    PENDING_REPLACE = "E"
    OMS_ALLOCATION_PENDING = "G"
    OMS_ALLOCATION_REJECT = "H"
    OMS_TRADE_REJECT = "J"
    OMS_ALLOCATION_REQUEST = "K"
    ELIGIBLE_FOR_CROSS = "L"
    PENDING_MODIFY = "M"
    OMS_TRADE_REQUEST = "T"


class OrdStatus(_StrEnum):
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    DONE_FOR_DAY = "3"
    CANCELED = "4"
    REPLACED = "5"
    PENDING_CANCEL = "6"
    REJECTED = "8"
    BILLABLE_CANCEL = "C"
    PENDING_REPLACE = "E"
    OMS_ALLOCATION_PENDING = "G"
    OMS_ALLOCATION_REJECT = "H"
    OMS_TRADE_REJECT = "J"
    OMS_ALLOCATION_REQUEST = "K"
    ELIGIBLE_FOR_CROSS = "L"
    PENDING_MODIFY = "M"
    OMS_TRADE_REQUEST = "T"


#: Order statuses after which the order is no longer live on the book.
TERMINAL_ORD_STATUSES = frozenset(
    {OrdStatus.FILLED, OrdStatus.CANCELED, OrdStatus.REPLACED, OrdStatus.REJECTED, OrdStatus.DONE_FOR_DAY}
)


class CxlRejResponseTo(_StrEnum):
    ORDER_CANCEL_REQUEST = "1"
    ORDER_CANCEL_REPLACE_REQUEST = "2"


class FlowIndicator(_StrEnum):
    NOT_THROTTLED = "0"
    THROTTLED = "1"


class WorkingAwayFromDisplay(_StrEnum):
    EQUAL = "0"
    DIFFERENT = "1"


class UnsolicitedAckType(_StrEnum):
    PRIORITY_UPDATE_NEW_ORDER_ID = "2"
    PRIORITY_UPDATE_SAME_ORDER_ID = "3"


class ParticipantType(_StrEnum):
    CUSTOMER = "1"
    MARKET_MAKER = "2"
    DMM = "3"
    SLP = "4"
    FLOOR_BROKER = "5"


class ExecutedTradingSession(_StrEnum):
    EARLY = "1"
    CORE = "2"
    LATE = "3"
    OVERNIGHT = "4"


class SessionStatus(_StrEnum):
    ACTIVE = "0"
    LOGOUT_COMPLETE = "4"
    INVALID_CREDENTIALS = "5"


class SessionRejectReason(_StrEnum):
    INVALID_TAG_NUMBER = "0"
    REQUIRED_TAG_MISSING = "1"
    TAG_NOT_DEFINED_FOR_MSG_TYPE = "2"
    UNDEFINED_TAG = "3"
    TAG_WITHOUT_VALUE = "4"
    VALUE_OUT_OF_RANGE = "5"
    INCORRECT_DATA_FORMAT = "6"
    DECRYPTION_PROBLEM = "7"
    SIGNATURE_PROBLEM = "8"
    COMPID_PROBLEM = "9"
    SENDING_TIME_ACCURACY = "10"
    INVALID_MSG_TYPE = "11"
    TAG_APPEARS_MORE_THAN_ONCE = "13"
    TAG_OUT_OF_ORDER = "14"
    REPEATING_GROUP_OUT_OF_ORDER = "15"
    OTHER = "99"


class CancelOnDisconnect(_StrEnum):
    """RawData (96) byte 1."""

    DISABLED = "0"
    CANCEL_DAY_DIRECTED = "1"
    CANCEL_ALL = "2"


class PriorityUpdateAcks(_StrEnum):
    """RawData (96) byte 2."""

    NOT_SUBSCRIBED = "0"
    SUBSCRIBED = "1"


class SessionSTP(_StrEnum):
    """RawData (96) byte 3: session-level default Self-Trade Prevention."""

    NONE = "T"
    CANCEL_NEWEST = "N"
    CANCEL_OLDEST = "O"
    CANCEL_BOTH = "C"
    CANCEL_DECREMENT = "D"

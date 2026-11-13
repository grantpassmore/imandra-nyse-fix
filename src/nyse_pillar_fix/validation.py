"""Pre-send validation of outbound messages against the Pillar FIX specification.

The gateway rejects messages that violate these rules; catching them client-side
avoids session-level rejects, which count toward the Denial-of-Service limit
(100 per SenderCompID/IP per day, section 5.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from . import tags as T
from .codec import Message
from .enums import (
    APP_MSG_TYPES,
    CROSS_SIDES,
    SHORT_SIDES,
    ExtendedExecInst,
    Market,
    OrdType,
    RoutingInst,
    TimeInForce,
)
from .spec import (
    AUCTION_OR_CROSS_MAX_ORDER_QTY,
    FIELDS,
    FORBIDDEN_CHARS,
    HEADER_IN,
    MAX_ORDER_QTY_FIELD,
    MAX_PRICE,
    MAX_PRICE_DECIMALS,
    MESSAGES,
    MIN_PRICE,
    STANDARD_MAX_ORDER_QTY,
    TEXT_LENGTH_BY_MSG_TYPE,
    FieldSpec,
)


@dataclass(frozen=True)
class Issue:
    tag: int | None
    message: str

    def __str__(self) -> str:
        if self.tag is None:
            return self.message
        return f"{T.NAMES.get(self.tag, '?')}({self.tag}): {self.message}"


class ValidationError(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues))


def max_order_qty(market: Market, msg: Message) -> int | None:
    """Exchange maximum OrderQty for *msg* on *market* (section 3), or ``None`` if uncapped."""
    side = msg.get(T.Side)
    tif = msg.get(T.TimeInForce)
    ext = msg.get(T.ExtendedExecInst)
    if market is Market.NYSE:
        if ext == ExtendedExecInst.ISSUER_DIRECT_OFFERING:
            return None
        auction = tif in (TimeInForce.AT_THE_OPENING, TimeInForce.AT_THE_CLOSE) or ext == (
            ExtendedExecInst.IMBALANCE_OFFSET
        )
        return AUCTION_OR_CROSS_MAX_ORDER_QTY if auction else STANDARD_MAX_ORDER_QTY
    if market is Market.TEXAS and side in CROSS_SIDES:
        return AUCTION_OR_CROSS_MAX_ORDER_QTY
    return STANDARD_MAX_ORDER_QTY


def _check_type(spec: FieldSpec, value: str, msg_type: str) -> str | None:
    length = spec.length
    if spec.tag == T.Text:
        length = TEXT_LENGTH_BY_MSG_TYPE.get(msg_type, length)
    if value == "":
        return "empty value"
    if spec.type in ("Int", "Qty"):
        if not value.isdigit():
            return f"expected an unsigned integer, got {value!r}"
    elif spec.type == "Price":
        try:
            d = Decimal(value)
        except InvalidOperation:
            return f"expected a decimal, got {value!r}"
        if not d.is_finite() or "e" in value.lower():
            return f"expected a plain decimal, got {value!r}"
        exp = d.as_tuple().exponent
        if isinstance(exp, int) and -exp > MAX_PRICE_DECIMALS:
            return f"at most {MAX_PRICE_DECIMALS} decimal places allowed, got {value!r}"
    elif spec.type == "Boolean":
        if value not in ("Y", "N"):
            return f"expected Y or N, got {value!r}"
    elif spec.type == "Char" and len(value) != 1 and spec.tag != T.RoutingStrategy:
        return f"expected a single character, got {value!r}"
    if length is not None and len(value) > length:
        return f"length {len(value)} exceeds maximum {length}"
    if not all(32 <= ord(c) < 127 for c in value):
        return "only printable ASCII is allowed"
    if spec.restricted_chars and any(c in FORBIDDEN_CHARS for c in value):
        return "contains a forbidden character (, ; | @ < > & ' \")"
    return None


def validate(msg: Message, market: Market | None = None, *, check_order_limits: bool = True) -> list[Issue]:
    """Return a list of problems with an outbound (firm -> gateway) *msg*.

    *market* enables per-market tag/value applicability checks. Header fields that the
    session layer stamps (MsgSeqNum, SenderCompID, TargetCompID, SendingTime) are not
    required here, so messages can be validated before they are sent.
    """
    issues: list[Issue] = []
    mt = msg.msg_type
    mspec = MESSAGES.get(mt)
    if mspec is None:
        return [Issue(T.MsgType, f"unsupported MsgType {mt!r}")]
    if "in" not in mspec.directions:
        return [Issue(T.MsgType, f"{mspec.name} is only sent by the gateway")]

    seen: set[int] = set()
    for tag, value in msg:
        if tag == T.MsgType:
            continue
        if tag in seen and tag != T.TradingSessionID:
            issues.append(Issue(tag, "tag appears more than once"))
        seen.add(tag)
        if tag not in HEADER_IN and tag not in mspec.body:
            issues.append(Issue(tag, f"tag not defined for {mspec.name}"))
            continue
        fspec = FIELDS.get(tag)
        if fspec is None:
            continue
        problem = _check_type(fspec, value, mt)
        if problem:
            issues.append(Issue(tag, problem))
            continue
        if market is not None:
            if market not in fspec.markets:
                issues.append(Issue(tag, f"not supported on {market.name}"))
                continue
            allowed = fspec.allowed_values(market)
            if allowed is not None and value not in allowed:
                issues.append(Issue(tag, f"value {value!r} not allowed on {market.name}; allowed: {sorted(allowed)}"))
        elif fspec.values is not None and value not in fspec.values:
            issues.append(Issue(tag, f"value {value!r} not allowed; allowed: {sorted(fspec.values)}"))

    for tag in sorted(mspec.required):
        if tag not in msg:
            issues.append(Issue(tag, "required tag missing"))

    target_sub = msg.get(T.TargetSubID)
    if target_sub is not None:
        if target_sub != "RET":
            issues.append(Issue(T.TargetSubID, 'only "RET" (retail billing) is accepted'))
        elif market is Market.TEXAS:
            issues.append(Issue(T.TargetSubID, "RET is not valid on NYSE Texas"))

    if mt in APP_MSG_TYPES and T.OnBehalfOfCompID not in msg:
        issues.append(Issue(T.OnBehalfOfCompID, "MPID is required on application messages"))

    if mt in ("D", "G"):
        issues.extend(_validate_order(msg, market, check_order_limits))
    elif mt == "F":
        issues.extend(_validate_cancel(msg))
    elif mt == "A":
        issues.extend(_validate_logon(msg))
    elif mt == "4":
        if msg.get(T.NewSeqNo, "0").isdigit() and int(msg.get(T.NewSeqNo, "0")) < 1:
            issues.append(Issue(T.NewSeqNo, "must be >= 1"))
    elif mt == "2":
        b = msg.get(T.BeginSeqNo, "")
        if b.isdigit() and int(b) < 1:
            issues.append(Issue(T.BeginSeqNo, "must be >= 1"))
    return issues


def _validate_order(msg: Message, market: Market | None, check_limits: bool) -> list[Issue]:
    issues: list[Issue] = []
    tags = msg.tags()
    if T.NoTradingSessions in tags and T.TradingSessionID in tags:
        if tags.index(T.TradingSessionID) != tags.index(T.NoTradingSessions) + 1:
            issues.append(Issue(T.TradingSessionID, "must immediately follow NoTradingSessions (386)"))
        if len(msg.get_all(T.TradingSessionID)) != 1:
            issues.append(Issue(T.TradingSessionID, "exactly one TradingSessionID is supported"))

    ord_type = msg.get(T.OrdType)
    if ord_type in (OrdType.LIMIT, OrdType.INSIDE_LIMIT) and T.Price not in msg:
        issues.append(Issue(T.Price, f"required for OrdType {ord_type}"))

    price = msg.get(T.Price)
    if price is not None:
        try:
            p = Decimal(price)
            if not (MIN_PRICE <= p <= MAX_PRICE):
                issues.append(Issue(T.Price, f"must be between {MIN_PRICE} and {MAX_PRICE}"))
        except InvalidOperation:
            pass

    qty_s = msg.get(T.OrderQty, "")
    qty = int(qty_s) if qty_s.isdigit() else None
    if qty is not None:
        if not 1 <= qty <= MAX_ORDER_QTY_FIELD:
            issues.append(Issue(T.OrderQty, f"must be between 1 and {MAX_ORDER_QTY_FIELD:,}"))
        elif check_limits and market is not None:
            cap = max_order_qty(market, msg)
            if cap is not None and qty > cap:
                issues.append(Issue(T.OrderQty, f"exceeds the {market.name} maximum of {cap:,} shares"))

    min_qty = msg.get(T.MinQty, "")
    if min_qty.isdigit() and qty is not None and int(min_qty) > qty:
        issues.append(Issue(T.MinQty, "must be <= OrderQty"))
    if msg.get(T.RoutingInst) == RoutingInst.MINIMUM_FILL and not (min_qty.isdigit() and int(min_qty) > 0):
        issues.append(Issue(T.MinQty, "RoutingInst 8 (Minimum Fill) requires a non-zero MinQty"))

    side = msg.get(T.Side)
    if side in SHORT_SIDES and msg.get(T.LocateReqd) != "N":
        issues.append(Issue(T.LocateReqd, "short sales must be sent with LocateReqd=N"))

    if msg.get(T.RoutingInst) == RoutingInst.ROUTE_TO_ALGO and T.DeliverToCompID not in msg:
        issues.append(Issue(T.DeliverToCompID, "RoutingInst E (Route to Algo) requires the Algo MPID"))
    if msg.get(T.RouteToBroker) == "Y" and T.DeliverToCompID not in msg:
        issues.append(Issue(T.DeliverToCompID, "RouteToBroker=Y requires the IB Firm Identifier"))
    if msg.get(T.SpecialOrdType) == "T" and T.DeliverToCompID not in msg:
        issues.append(Issue(T.DeliverToCompID, "SpecialOrdType T (QCT) requires the IB Firm Identifier"))
    if side in CROSS_SIDES and market not in (None, Market.TEXAS):
        issues.append(Issue(T.Side, "cross sides are only supported on NYSE Texas"))

    offset = msg.get(T.OffsetPrice)
    if offset is not None:
        try:
            o = Decimal(offset)
            if o != 0:
                step = Decimal("0.01") if msg.get(T.ExecInst) == "P" else Decimal("0.001")
                if o % step != 0:
                    issues.append(Issue(T.OffsetPrice, f"must be zero or a multiple of {step}"))
        except InvalidOperation:
            pass
    return issues


def _validate_cancel(msg: Message) -> list[Issue]:
    issues: list[Issue] = []
    bulk = T.OrderID in msg
    if bulk:
        from .enums import BulkCancelCode

        code = msg.get(T.OrderID)
        if code not in {c.value for c in BulkCancelCode}:
            issues.append(Issue(T.OrderID, f"invalid bulk cancel code {code!r}"))
        side = msg.get(T.Side)
        if side is not None and side not in ("1", "2"):
            issues.append(Issue(T.Side, "bulk cancel supports only 1 (Buy) or 2 (Sell)"))
        if T.OrigClOrdID in msg:
            issues.append(Issue(T.OrigClOrdID, "must not be combined with a bulk cancel code"))
    else:
        for tag in (T.OrigClOrdID, T.Side, T.Symbol):
            if tag not in msg:
                issues.append(Issue(tag, "required for single order cancellation"))
    return issues


def _validate_logon(msg: Message) -> list[Issue]:
    issues: list[Issue] = []
    hb = msg.get(T.HeartBtInt, "")
    if hb.isdigit() and not 1 <= int(hb) <= 60:
        issues.append(Issue(T.HeartBtInt, "must be between 1 and 60 seconds"))
    if T.Password not in msg:
        issues.append(Issue(T.Password, "required on the Logon request"))
    has_raw, has_len = T.RawData in msg, T.RawDataLength in msg
    if has_raw != has_len:
        issues.append(Issue(T.RawDataLength, "RawData and RawDataLength must be sent together"))
    if has_raw:
        from .session_config import SessionSettings

        try:
            SessionSettings.decode(msg[T.RawData])
        except ValueError as e:
            issues.append(Issue(T.RawData, str(e)))
    return issues


def validate_or_raise(msg: Message, market: Market | None = None, **kw) -> None:
    issues = validate(msg, market, **kw)
    if issues:
        raise ValidationError(issues)

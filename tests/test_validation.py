from decimal import Decimal

import pytest

from nyse_pillar_fix import tags as T
from nyse_pillar_fix.codec import Message
from nyse_pillar_fix.enums import (
    BulkCancelCode,
    ExecInst,
    ExtendedExecInst,
    Market,
    OrdType,
    RoutingInst,
    Side,
    TimeInForce,
    TradingSessionID,
)
from nyse_pillar_fix.messages import NewOrderSingle, OrderCancelReplaceRequest, OrderCancelRequest, Routing
from nyse_pillar_fix.validation import ValidationError, validate, validate_or_raise

R = Routing(mpid="ABCD")


def order(**kw):
    base = dict(cl_ord_id="C1", symbol="IBM", side=Side.BUY, order_qty=100, price=Decimal("10.5"), routing=R)
    base.update(kw)
    return NewOrderSingle(**base).to_message()


def issue_tags(msg, market=None):
    return {i.tag for i in validate(msg, market)}


def test_valid_limit_order_has_no_issues():
    assert validate(order(), Market.NYSE) == []
    assert validate(order(), Market.ARCA) == []


def test_required_fields_and_mpid():
    m = order()
    m.remove(T.Symbol).remove(T.OnBehalfOfCompID)
    assert {T.Symbol, T.OnBehalfOfCompID} <= issue_tags(m)


def test_undefined_tag_for_message_type_is_rejected():
    m = order()
    m.append(T.CxlRejResponseTo, "1")
    assert T.CxlRejResponseTo in issue_tags(m)


def test_limit_requires_price_and_price_precision():
    assert T.Price in issue_tags(order(price=None, ord_type=OrdType.LIMIT))
    assert validate(order(price=None, ord_type=OrdType.MARKET)) == []
    assert T.Price in issue_tags(order(price=Decimal("1.0000001")))


def test_short_sale_requires_locate_reqd_n():
    m = order(side=Side.SELL_SHORT)
    assert m[T.LocateReqd] == "N"  # filled in automatically
    m.set(T.LocateReqd, "Y")
    assert T.LocateReqd in issue_tags(m)


def test_market_applicability():
    # Last Sale Peg is NYSE only; Tracking order is not available on NYSE
    assert T.ExecInst in issue_tags(order(exec_inst=ExecInst.LAST_SALE_PEG), Market.ARCA)
    assert validate(order(exec_inst=ExecInst.LAST_SALE_PEG), Market.NYSE) == []
    assert T.ExecInst in issue_tags(order(exec_inst=ExecInst.TRACKING_ORDER), Market.NYSE)
    # Overnight session only on Arca; Late session not on NYSE
    assert validate(order(trading_session_id=TradingSessionID.OVERNIGHT), Market.ARCA) == []
    assert T.TradingSessionID in issue_tags(order(trading_session_id=TradingSessionID.OVERNIGHT), Market.NATIONAL)
    assert T.TradingSessionID in issue_tags(order(trading_session_id=TradingSessionID.LATE), Market.NYSE)
    # Cross sides are NYSE Texas only
    assert validate(order(side=Side.CROSS), Market.TEXAS) == []
    assert T.Side in issue_tags(order(side=Side.CROSS), Market.ARCA)
    # Algo fields are NYSE only
    assert T.TargetStrategy in issue_tags(order(target_strategy="VWAP"), Market.ARCA)
    # Retail Order Type 2 is NYSE National only
    assert validate(order(extended_exec_inst=ExtendedExecInst.RETAIL_ORDER_TYPE_2), Market.NATIONAL) == []
    assert T.ExtendedExecInst in issue_tags(order(extended_exec_inst=ExtendedExecInst.RETAIL_ORDER_TYPE_2), Market.NYSE)


def test_retail_target_sub_id():
    m = order(routing=Routing(mpid="ABCD", retail=True))
    assert m[T.TargetSubID] == "RET"
    assert validate(m, Market.NYSE) == []
    assert T.TargetSubID in issue_tags(m, Market.TEXAS)


def test_forbidden_characters_and_lengths():
    assert T.ClOrdID in issue_tags(order(cl_ord_id="A,B"))
    assert T.ClOrdID in issue_tags(order(cl_ord_id="X" * 21))
    assert T.Account in issue_tags(order(account="a@b"))
    assert T.Text in issue_tags(order(text="x" * 81))


def test_quantity_limits():
    assert T.OrderQty in issue_tags(order(order_qty=0))
    assert T.OrderQty in issue_tags(order(order_qty=5_000_001), Market.ARCA)
    # NYSE auction orders may go to 25,000,000
    assert validate(order(order_qty=20_000_000, time_in_force=TimeInForce.AT_THE_CLOSE), Market.NYSE) == []
    assert T.OrderQty in issue_tags(order(order_qty=20_000_000), Market.NYSE)
    assert validate(order(order_qty=20_000_000), Market.NYSE, check_order_limits=False) == []
    assert T.MinQty in issue_tags(order(min_qty=200))
    assert T.MinQty in issue_tags(order(routing_inst=RoutingInst.MINIMUM_FILL))


def test_trading_session_group_order():
    m = order()
    tsid = m[T.TradingSessionID]
    m.remove(T.TradingSessionID)
    m.append(T.TradingSessionID, tsid)  # now after OrderCapacity
    assert T.TradingSessionID in issue_tags(m)


def test_cancel_single_and_bulk():
    single = OrderCancelRequest(cl_ord_id="X2", orig_cl_ord_id="X1", side=Side.BUY, symbol="IBM", routing=R)
    assert validate(single.to_message(), Market.NYSE) == []
    missing = OrderCancelRequest(cl_ord_id="X2", orig_cl_ord_id="X1", routing=R)
    assert {T.Side, T.Symbol} <= issue_tags(missing.to_message())
    bulk = OrderCancelRequest(cl_ord_id="X3", bulk_cancel_code=BulkCancelCode.MPID_DAY, side=Side.SELL, routing=R)
    assert validate(bulk.to_message()) == []
    bad = OrderCancelRequest(cl_ord_id="X3", bulk_cancel_code="6", side=Side.SELL_SHORT, routing=R)
    assert {T.OrderID, T.Side} <= issue_tags(bad.to_message())


def test_replace_requires_orig_and_rejects_settlement_type():
    rep = OrderCancelReplaceRequest(
        cl_ord_id="C2", orig_cl_ord_id="C1", symbol="IBM", side=Side.BUY, order_qty=50, price=Decimal("10"), routing=R
    )
    assert validate(rep.to_message(), Market.NYSE) == []
    with pytest.raises(ValueError):
        OrderCancelReplaceRequest(
            cl_ord_id="C2",
            orig_cl_ord_id="C1",
            symbol="IBM",
            side=Side.BUY,
            order_qty=50,
            settlement_type="0",
            routing=R,
        )


def test_logon_validation():
    m = Message("A", [(T.EncryptMethod, 0), (T.HeartBtInt, 90), (T.Username, "U"), (T.RawData, "39X")])
    tags = issue_tags(m)
    assert {T.HeartBtInt, T.Password, T.RawDataLength, T.RawData} <= tags


def test_gateway_only_messages_cannot_be_sent():
    with pytest.raises(ValidationError):
        validate_or_raise(Message("8", [(T.ClOrdID, "X")]))

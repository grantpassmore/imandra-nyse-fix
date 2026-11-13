# nyse-pillar-fix

An asyncio Python client for the **NYSE Pillar Gateway FIX protocol**: FIX 4.2 with Pillar's own tags and rules. It follows the
[NYSE Pillar Gateway FIX Protocol Specification, version 6.0 (August 14, 2026)](https://www.nyse.com/publicdocs/nyse/NYSE_Pillar_Gateway_FIX_Protocol_Specification.pdf)
and supports NYSE, NYSE Arca, NYSE American, NYSE National and NYSE Texas equities.

The package has no runtime dependencies and needs Python 3.10 or later.

## Features

| Area | What is implemented | Spec section |
|---|---|---|
| Codec | Tag=value encoding with BodyLength and CheckSum, header fields placed first, an incremental stream parser, and timestamps from milliseconds to nanoseconds | 6 |
| Session | Logon (with RawData session settings), heartbeats, Test Requests, gap detection and Resend Requests, Sequence Reset (gap-fill and reset), Logout, and Logon Reject (`SessionStatus=5`) | 7 |
| Pillar sequencing rules | Messages with a low MsgSeqNum and PossDup=Y are ignored, Logon/Resend Request are still handled when MsgSeqNum is too high, the sender sequence follows `NextExpectedMsgSeqNum (789)`, and `ResetSeqNumFlag` is never sent | 7.1 |
| Failover | Tries the primary endpoint first, then the secondary | 1.3 |
| Orders | New Order Single (D), Cancel (F), Bulk Cancel and block/unblock (F with a code in OrderID), Cancel/Replace and Modify (G) | 8.1–8.3 |
| Responses | Typed `ExecutionReport` (acks, fills, busts, corrections, Billable Cancel, Order Priority Update) and `OrderCancelReject`, with Pillar reason codes parsed | 8.4–8.5 |
| Validation | Checks before sending: which tags each message allows, required tags, type and length limits, forbidden characters, and which values each market accepts. Also enforces conditional rules (Price for limit orders, LocateReqd=N on short sales, MinQty for Minimum Fill, RET only off NYSE Texas, and so on) and the maximum price and quantity | 2, 3, 8 |
| Throttling | Client-side pacing to 500 messages per rolling 100 ms | 5.2 |
| XDP mapping | Splits a DealID into SystemID, MarketID and TradeID, and maps OrderID to the XDP OrderID | 4 |
| Persistence | `MemoryStore` and `FileStore`, which keep sequence numbers and sent application messages | – |
| Testing | `nyse_pillar_fix.testing.MockPillarGateway`, an in-process gateway that follows the Pillar session rules | – |

## Install

```bash
pip install "git+https://github.com/grantpassmore/imandra-nyse-fix.git"
# or, for development
git clone https://github.com/grantpassmore/imandra-nyse-fix.git && cd imandra-nyse-fix
python -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

## Quick start

```python
import asyncio
from decimal import Decimal

from nyse_pillar_fix import (
    CancelOnDisconnect, FileStore, Market, OrdStatus, PillarClient, SessionConfig,
    SessionSettings, SessionSTP, Side, TimeInForce,
)

config = SessionConfig(
    sender_comp_id="ABC1",            # agreed connection identifier
    target_comp_id=Market.ARCA,       # MIC of the target market
    username="ABC1",
    password="secret",
    endpoints=[("10.0.0.1", 6000), ("10.0.0.2", 6000)],   # primary, secondary
    heartbeat_interval=30,
    settings=SessionSettings(        # optional RawData (96) override
        cancel_on_disconnect=CancelOnDisconnect.CANCEL_ALL,
        self_trade_prevention=SessionSTP.CANCEL_NEWEST,
    ),
)

async def main():
    client = PillarClient(config, store=FileStore("./fix-state"), mpid="ABCD",
                          on_execution_report=lambda er: print(er))
    async with client:
        order = await client.new_order("IBM", Side.BUY, 100, price=Decimal("150.25"),
                                       time_in_force=TimeInForce.DAY)
        await order.wait_status(OrdStatus.NEW, timeout=5)

        await client.replace(order, order_qty=50)            # Modify, which keeps priority
        await client.cancel(order)
        await order.wait_status(OrdStatus.CANCELED, timeout=5)

asyncio.run(main())
```

You can also pass any field from the spec as a keyword argument:

```python
from nyse_pillar_fix import (BulkCancelCode, ExecInst, ExtendedExecInst, RoutingInst,
                             SelfTradeType, TradingSessionID)

await client.new_order(
    "XYZ", Side.SELL_SHORT, 500, price="12.01",
    locate_broker="ABCD",                       # LocateReqd=N is set for you on short sales
    exec_inst=ExecInst.NON_DISPLAYED,
    extended_exec_inst=ExtendedExecInst.ADD_LIQUIDITY_ONLY,
    routing_inst=RoutingInst.NON_ROUTABLE,
    self_trade_type=SelfTradeType.CANCEL_OLDEST,
    trading_session_id=TradingSessionID.EARLY_CORE_LATE,
)
await client.bulk_cancel(BulkCancelCode.MPID_DAY, side=Side.SELL)
```

### Lower-level use

`FixSession` handles only the session layer. `NewOrderSingle`, `OrderCancelRequest` and `OrderCancelReplaceRequest` each build a
`Message`, and `validate(msg, market)` returns a list of problems without raising:

```python
from nyse_pillar_fix import Market, NewOrderSingle, Routing, Side, validate
msg = NewOrderSingle(cl_ord_id="A1", symbol="IBM", side=Side.BUY, order_qty=100,
                     price="10", routing=Routing(mpid="ABCD")).to_message()
assert validate(msg, Market.NYSE) == []
print(msg.encode())
```

## Operational notes from the spec

* **Sequence numbers** start at 1 each trading day. Pillar rejects `ResetSeqNumFlag=Y`, so sequences are never reset through the
  session. Call `store.reset()` at the start of the day, or after a DR failover or a session restart that came with a software rollback
  (section 1.3).
* **Resend Requests from the gateway** are answered with gap-fills by default, so stale orders are never sent again. Set
  `resend_mode="resend"` to replay them with `PossDupFlag=Y`.
* **A Logon with too low a MsgSeqNum** is rejected by Pillar, which then disconnects. The client raises `LogonError`, and its
  `next_expected` attribute holds the number Pillar reported. Set `store.set_next_sender_seq(err.next_expected)` and reconnect.
* **Denial of Service mode**: after 100 logon attempts or 100 session-level rejects in a day, Pillar refuses the SenderCompID/IP
  for 60 s (section 5.3). Outbound validation keeps rejects from counting toward this limit. Use backoff when you reconnect.
* **Cancel on Disconnect** can only be raised through Logon RawData (0 → 1/2, 1 → 2). The section 7.2 example describes `"11C"` as
  "cancel all", but the value table defines byte 1 = `1` as *Cancel Day/Directed*. This library follows the table.
* The Liquidity Indicators, Pillar Reason Codes and Order Type Matrix appendices are maintained by NYSE as spreadsheets. They are not
  copied here. `ExecutionReport.reason_code` pulls the `Rxxx` code out of Text (58).

## Layout

```
src/nyse_pillar_fix/
  codec.py          FIX framing, encoding, decoding, timestamps
  tags.py           tag numbers
  enums.py          enumerated values (Side, OrdType, ExecType, BulkCancelCode, Market, ...)
  spec.py           field dictionary and message definitions, with per-market value applicability
  validation.py     pre-send validation
  messages.py       typed requests and responses
  session.py        asyncio session engine
  session_config.py Logon RawData settings
  client.py         high-level order client and order-state tracking
  store.py          sequence and message persistence
  throttle.py       rolling-window pacing
  xdp.py            DealID/OrderID to XDP mapping
  testing.py        MockPillarGateway
```

## Disclaimer

This is not NYSE software. Do certification testing with NYSE before you connect to production.

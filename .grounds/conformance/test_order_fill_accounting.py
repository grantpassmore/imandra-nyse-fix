"""Binding: ImandraX vectors for `step_fixed` (subject "order fill accounting") run against
OrderState.apply_fill_accounting.

Mapping between the model and the code:

* ``s.f_cum``  -> ``OrderState.cum_qty``
* ``s.fills``  -> ``OrderState.fills`` (ExecID -> (LastQty, LastPx)); the model's list of fills
  represents a map with unique ExecIDs, so both sides are compared sorted by ExecID.
* ``r``        -> an ``ExecutionReport`` parsed from a FIX 35=8 message:
  trans TNew/TBust/TCorrect -> ExecTransType 0/1/2, kind KFill/KOther -> ExecType 1/0.
* Prices are integers in millionths of a dollar in the model and Decimals in the code.

Run with: python -m pytest .grounds/conformance/test_order_fill_accounting.py -v
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from nyse_pillar_fix import tags as T
from nyse_pillar_fix.client import OrderState
from nyse_pillar_fix.codec import Message
from nyse_pillar_fix.messages import ExecutionReport, NewOrderSingle, Routing

VECTORS = json.loads((Path(__file__).parent / "order_fill_accounting.step_fixed.vectors.json").read_text())["vectors"]

TRANS = {"TNew": "0", "TBust": "1", "TCorrect": "2"}
KIND = {"KFill": "1", "KOther": "0"}
MICRO = Decimal("0.000001")


def to_price(micros: int) -> Decimal:
    return Decimal(micros) * MICRO


def from_price(px: Decimal) -> int:
    value = px / MICRO
    assert value == value.to_integral_value(), f"price {px} is not a whole number of millionths"
    return int(value)


def to_option(v):
    return None if v["$c"] == "None" else v["args"][0]


def build_state(s: dict) -> OrderState:
    req = NewOrderSingle(cl_ord_id="O1", symbol="IBM", side="1", order_qty=100, price="1", routing=Routing(mpid="X"))
    state = OrderState(request=req, cl_ord_id="O1", cum_qty=s["f_cum"])
    for f in s["fills"]:
        state.fills[str(f["fid"])] = (f["fqty"], to_price(f["fpx"]))
    return state


def build_report(r: dict) -> ExecutionReport:
    fields = [
        (T.ClOrdID, "O1"),
        (T.ExecTransType, TRANS[r["trans"]["$c"]]),
        (T.ExecType, KIND[r["kind"]["$c"]]),
        (T.OrdStatus, KIND[r["kind"]["$c"]]),
        (T.ExecID, r["exec_id"]),
        (T.ExecRefID, r["ref_id"]),
        (T.LastQty, r["last_qty"]),
        (T.LastPx, to_price(r["last_px"])),
    ]
    cum = to_option(r["cum_qty"])
    if cum is not None:
        fields.append((T.CumQty, cum))
    return ExecutionReport.from_message(Message("8", fields))


def read_back(state: OrderState) -> dict:
    fills = [{"fid": int(k), "fqty": q, "fpx": from_price(p)} for k, (q, p) in state.fills.items()]
    return {"f_cum": state.cum_qty, "fills": sorted(fills, key=lambda f: f["fid"])}


@pytest.mark.parametrize("v", VECTORS, ids=[v["id"] for v in VECTORS])
def test_vector(v):
    state = build_state(v["input"]["s"])
    state.apply_fill_accounting(build_report(v["input"]["r"]))
    expected = {"f_cum": v["expected"]["f_cum"], "fills": sorted(v["expected"]["fills"], key=lambda f: f["fid"])}
    assert read_back(state) == expected

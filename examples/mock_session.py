"""End-to-end demo against the in-process mock gateway.

    python examples/mock_session.py
"""

import asyncio
import logging
from decimal import Decimal

from nyse_pillar_fix import Market, OrdStatus, PillarClient, SessionConfig, Side
from nyse_pillar_fix.testing import MockPillarGateway


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async with MockPillarGateway(Market.NYSE, {"DEMO1": "pw"}) as gw:
        config = SessionConfig(
            sender_comp_id="DEMO1",
            target_comp_id=Market.NYSE,
            username="DEMO1",
            password="pw",
            endpoints=[gw.endpoint],
        )
        async with PillarClient(config, mpid="DEMO", on_execution_report=print) as client:
            order = await client.new_order("IBM", Side.BUY, 100, price=Decimal("150.25"))
            await order.wait_status(OrdStatus.NEW, timeout=5)
            new_id = await client.replace(order, order_qty=50)
            await order.wait_for(lambda o: o.cl_ord_id == new_id, timeout=5)
            await client.cancel(order)
            await order.wait_status(OrdStatus.CANCELED, timeout=5)
            print("final:", order)


if __name__ == "__main__":
    asyncio.run(main())

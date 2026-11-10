"""FastAPI runs plain `def` routes in worker threads, all sharing one Store.

The dashboard loads wallet, allowances and audit in parallel, which once
crashed with a half-read row ("conversion from NoneType to Decimal").
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from app.adapters.store import Store
from app.domain.models import Allowance
from app.domain.money import Money


def test_store_survives_concurrent_threads(tmp_path):
    store = Store(tmp_path / "threads.db")
    usd = lambda v: Money.of(v, "USD")  # noqa: E731
    for n in range(5):
        store.create_allowance(Allowance(
            id=f"alw_{n}", budget=usd("120"), per_purchase_limit=usd("100"),
            approval_threshold=usd("50"), expires_at=datetime.now(UTC) + timedelta(days=1),
            spent=usd("0"), reserved=usd("0"),
        ))

    def dashboard_refresh(i: int) -> None:
        store.append_audit("test", {"i": i})
        for allowance_id in store.list_allowance_ids():
            assert store.get_allowance(allowance_id).budget == usd("120")
        store.get_wallet()
        store.list_audit()

    with ThreadPoolExecutor(max_workers=16) as pool:
        for future in [pool.submit(dashboard_refresh, i) for i in range(400)]:
            future.result()  # re-raises any error from the worker thread
    store.close()

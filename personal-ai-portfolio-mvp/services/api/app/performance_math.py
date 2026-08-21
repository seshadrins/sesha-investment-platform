from __future__ import annotations

from datetime import date
from decimal import Decimal


def xirr(cash_flows: list[tuple[date, Decimal]]) -> Decimal | None:
    """Money-weighted return via bisection search on the internal rate of return.

    Shared by the notional-portfolio and real-ledger performance modules so both compute
    money-weighted return the same way instead of maintaining two copies of this bisection.
    """
    if len(cash_flows) < 2 or cash_flows[-1][0] <= cash_flows[0][0]:
        return None
    origin = cash_flows[0][0]

    def npv(rate: float) -> float:
        return sum(float(amount) / ((1 + rate) ** ((day - origin).days / 365))
                   for day, amount in cash_flows)

    low, high = -.9999, 10.0
    if npv(low) * npv(high) > 0:
        return None
    for _ in range(100):
        middle = (low + high) / 2
        if npv(low) * npv(middle) <= 0:
            high = middle
        else:
            low = middle
    return Decimal(str((low + high) / 2))

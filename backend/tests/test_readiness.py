from decimal import Decimal

from app.services.readiness import PHASE_WEIGHTS


def test_phase_weights_sum_to_one():
    total = sum(PHASE_WEIGHTS.values())
    assert total == Decimal("1.00") or abs(float(total) - 1.0) < 1e-9

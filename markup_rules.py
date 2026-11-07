"""見積書の共通配分ルール。単価×数量と指定上乗せ額を同時に守る。"""
from fractions import Fraction
from decimal import Decimal, ROUND_HALF_UP
import math

LUMP_STEP = 5000
LUMP_MAX_ADD = 50000
LUMP_MAX_RATIO = 0.5
RULE_VERSION = "2026-11-07.1"
RULE_DESCRIPTION = "全体の上乗せ額は原価比で分散し、自然な単価を優先。一式は5,000円刻み、業者別・工種別では原価50％以内・最大5万円。固定単価は維持し、値引きは選んだ案だけ反映します。"


def fixed_price(value=False):
    """Missing flags are false; strings/numbers cannot silently lock a price."""
    import pandas as pd
    import numpy as np
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return False
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError('単価固定は確認済みのチェック値で指定してください。')
    return bool(value)


def natural_rate_tick(rate):
    """Unit-independent protection uses line costs; this is display rounding only."""
    return 1 if rate < 200 else 5 if rate < 1000 else 10 if rate < 3000 else 100 if rate < 50000 else 1000


def measured_add_cap(amount, profit_ratio, override=False):
    # Smooth across 499/500/999/1000/2999/3000 yen and kg/ton representations.
    return None if override else int(Decimal(str(amount)) * max(Decimal('.5'), min(Decimal(1), Decimal(str(profit_ratio)) * 2)))


def line_amount(qty, rate):
    """Excel/Numbers ROUND convention, including half-yen and negative values."""
    return int((Decimal(str(qty)) * Decimal(str(rate))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def exact_remainder(remaining, candidates):
    """Bounded coin allocation with bitsets; never dump a remainder into a lump sum.

    candidates: (row index, amount quantum, available amount), least preferred first.
    """
    if remaining == 0:
        return {}
    divisor = 0
    for _, step, capacity in candidates:
        if capacity >= step:
            divisor = math.gcd(divisor, step)
    if not divisor or remaining % divisor:
        return None
    target = remaining // divisor
    if target > 2_000_000:
        return None  # A bounded rounding repair, never an unbounded full-budget search.
    mask = (1 << (target + 1)) - 1
    reachable = 1
    history = []
    for idx, step, capacity in candidates:
        count = min(capacity // step, remaining // step)
        chunk = 1
        while count:
            used = min(chunk, count)
            shift = step * used // divisor
            history.append((reachable, idx, step * used, shift))
            reachable = (reachable | (reachable << shift)) & mask
            count -= used
            chunk *= 2
    if not ((reachable >> target) & 1):
        return None
    result = {}
    for before, idx, amount, shift in reversed(history):
        if target >= shift and ((before >> (target - shift)) & 1):
            result[idx] = result.get(idx, 0) + amount
            target -= shift
    return result


def quantity_steps(qty, lump):
    fraction = Fraction(str(qty)).limit_denominator(1000)
    if abs(float(fraction) - qty) > 1e-9 or fraction <= 0:
        raise ValueError("数量の精度を確認してください。小数点以下3桁までに対応しています。")
    minimum_rate_step = math.lcm(fraction.denominator, LUMP_STEP) if lump else fraction.denominator
    natural_rate_step = minimum_rate_step if lump else math.lcm(minimum_rate_step, 100)
    # First prefer 100-yen rate increments; small measured quantities can
    # absorb the final yen with a two-decimal rate without changing the total.
    minimum_amount_step = (1 if qty <= 100 else fraction.numerator // math.gcd(fraction.numerator, fraction.denominator*100)) if not lump else int(fraction * minimum_rate_step)
    return minimum_amount_step, int(fraction * natural_rate_step)

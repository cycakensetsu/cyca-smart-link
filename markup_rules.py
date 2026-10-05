"""見積書の共通配分ルール。単価×数量と指定上乗せ額を同時に守る。"""
from fractions import Fraction
from decimal import Decimal, ROUND_HALF_UP
import math

LUMP_STEP = 5000
LUMP_MAX_ADD = 50000
LUMP_MAX_RATIO = 0.5
RULE_VERSION = "2026-10-05"
RULE_DESCRIPTION = "一式は5,000円刻み・原価の50％以内・最大5万円。実数量の高単価工事を優先し、指定額と明細合計を一致させます。"


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
    minimum_amount_step = 1 if not lump and qty <= 100 else int(fraction * minimum_rate_step)
    return minimum_amount_step, int(fraction * natural_rate_step)

"""Detect a narrow printed-quantity rounding case without rewriting source facts."""
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import re,unicodedata


def printed_rounding(row):
    try:
        qty=Decimal(str(row.get('数量')));rate=Decimal(str(row.get('原価単価')));amount=Decimal(str(row.get('原価金額')))
        if not all(v.is_finite() for v in (qty,rate,amount)) or qty<=0 or rate<=0 or amount<=0:return None
        if qty==qty.to_integral_value():return None
        decimals=-qty.as_tuple().exponent
        if not 1<=decimals<=3:return None
        expected=(qty*rate).quantize(Decimal('1'),rounding=ROUND_HALF_UP)
        delta=amount-expected
        if not delta or abs(delta)>rate*Decimal(10)**(-decimals)/2+1:return None
        text=unicodedata.normalize('NFKC',str(row.get('抽出元テキスト範囲') or '')).replace(',','')
        for value in (qty,rate,amount):
            token=format(value.normalize(),'f')
            if not re.search(r'(?<![\d.])'+re.escape(token)+r'(?:\.0+)?(?![\d.])',text):return None
        return int(delta)
    except (InvalidOperation,ValueError,TypeError):return None

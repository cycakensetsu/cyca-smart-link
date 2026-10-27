"""Shared physical text space for PDF and the imported Numbers worksheet."""
from functools import lru_cache
import math
import unicodedata


@lru_cache(maxsize=4096)
def cell_height(value, column):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from xml.sax.saxutils import escape
    font='HeiseiKakuGo-W5'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    pdf_width={2:192,3:162,8:175.89}[column]-8
    style=ParagraphStyle('measure',fontName=font,fontSize=9,leading=11,wordWrap='CJK')
    paragraph=Paragraph(escape(value).replace('\n','<br/>'),style)
    _,height=paragraph.wrap(pdf_width,100000)
    available=({2:34,3:30,8:32}[column]*7+5)*.75-12
    lines=sum(max(1,math.ceil(sum(0 if unicodedata.combining(c) else
                (1 if unicodedata.east_asian_width(c) in ('W','F','A') else .6)
                for c in line)*9/available)) for line in value.split('\n'))
    # Numbers imports the Japanese fallback font with about 1.7x line spacing;
    # 4/3 spacing clipped a real window specification's final sash dimensions.
    return max(22,math.ceil(height+8),math.ceil(lines*9*1.75+8) if lines>1 else 22)


def values_height(values):
    return max([22]+[cell_height(str(values[c-1]),c) for c in (2,3,8)
                     if len(values)>=c and values[c-1] not in ('',None)])


def row_values(row):
    kind=row['kind']
    if kind=='item':
        item=row['item']
        return [row['no'],item.name,item.spec,item.qty,item.unit,item.unit_price,item.amount,item.remark]
    if kind=='category':return ['',f"{row['index']}. {row['category'].name}"]
    if kind=='section':return ['','【'+row['label']+'】']
    return []


def entry_height(row):
    return values_height(row_values(row))


def grid_fits(heights, count=20):
    """Spend empty row space on very long text, without font shrinking."""
    blank=2 if heights and max(heights)>160 else 8
    return (len(heights)<=count
        and sum(heights)+blank*(count-len(heights))<=count*22
        and sum(21 if h==22 else h for h in heights)+blank*(count-len(heights))<=count*21)

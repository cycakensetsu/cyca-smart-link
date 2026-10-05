"""One physical page per visible sheet; PDF output shares the same page plan."""
import re
from copy import copy
from io import BytesIO
from openpyxl.formula.tokenizer import Tokenizer
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.page import PageMargins


def split_print_pages(wb, estimate):
    """Numbers ignores Excel manual breaks and imports hidden sheets as visible.

    Split the editable quote into actual worksheets and remove the redundant raw
    sheet, so neither empty detail blocks nor raw data can become printed pages.
    Translate every formula reference by its original cell, including subtotals
    crossing detail pages. No amounts become fixed overrides.
    """
    from template_fill_test_v2 import (QUOTE_SHEET, SUMMARY_SHEET, DETAIL_SHEET,
        DETAIL_PAGE_START_ROW, DETAIL_PAGE_BLOCK_ROWS, MAX_DETAIL_PAGES,
        COVER_END_ROW, SUMMARY_BAND_ROW, SUMMARY_PAGE_END_ROW)
    source = wb[QUOTE_SHEET]
    blocks = [(QUOTE_SHEET, 1, COVER_END_ROW),
              (SUMMARY_SHEET, SUMMARY_BAND_ROW, SUMMARY_PAGE_END_ROW)]
    for i in range(MAX_DETAIL_PAGES):
        start = DETAIL_PAGE_START_ROW + i * DETAIL_PAGE_BLOCK_ROWS
        if not source.row_dimensions[start].hidden:
            blocks.append((f"明細{i+1}", start, start+DETAIL_PAGE_BLOCK_ROWS-1))
    source.title = "_layout_source"
    if DETAIL_SHEET in wb:
        del wb[DETAIL_SHEET]
    for name, start, end in blocks:
        dest = wb.create_sheet(name)
        dest.sheet_view.showGridLines = False
        for key, dimension in source.column_dimensions.items():
            dest.column_dimensions[key] = copy(dimension)
        for old_row in range(start, end+1):
            row = old_row-start+1
            dest.row_dimensions[row].height = source.row_dimensions[old_row].height
            for old in source[old_row][:8]:
                cell = dest.cell(row, old.column, old.value)
                cell._style = copy(old._style)
                cell.alignment = copy(old.alignment)
        for merged in source.merged_cells.ranges:
            if start <= merged.min_row and merged.max_row <= end:
                dest.merge_cells(start_row=merged.min_row-start+1,
                    end_row=merged.max_row-start+1, start_column=merged.min_col,
                    end_column=merged.max_col)
        if name == QUOTE_SHEET:
            from template_fill_test_v2 import LOGO_PATH
            from openpyxl.drawing.image import Image
            if LOGO_PATH.exists():
                logo=Image(str(LOGO_PATH)); logo.width,logo.height=186,67
                dest.add_image(logo,"F3")
        dest.page_setup.orientation = "landscape"
        dest.page_setup.paperSize = dest.PAPERSIZE_A4
        dest.page_setup.fitToWidth = 1
        dest.page_setup.fitToHeight = 1
        dest.sheet_properties.pageSetUpPr.fitToPage = True
        dest.page_margins = PageMargins(left=.28,right=.28,top=.28,bottom=.28,header=.12,footer=.12)
        dest.print_area = f"A1:H{end-start+1}"
        dest.oddFooter.left.text = estimate.project_name
        dest.oddFooter.left.size = 8
        dest.oddFooter.right.text = f"{len(wb.worksheets)-1} / {len(blocks)}"
        dest.oddFooter.right.size = 8
    def map_cell(address):
        match = re.fullmatch(r"(\$?[A-H])(\$?)(\d+)", address)
        if not match:
            raise ValueError(f"印刷用数式の参照を移せません: {address}")
        col, absolute, number = match.groups(); number=int(number)
        for name,start,end in blocks:
            if start <= number <= end:
                return name, f"{col}{absolute}{number-start+1}"
        raise ValueError(f"印刷ページ外の参照があります: {address}")
    for name,_,_ in blocks:
        ws=wb[name]
        for row in ws:
            for cell in row:
                if cell.data_type != 'f': continue
                tokens=Tokenizer(cell.value).items
                for token in tokens:
                    if token.type!='OPERAND' or token.subtype!='RANGE': continue
                    ref=token.value
                    if '!' in ref:
                        owner,ref=ref.rsplit('!',1)
                        if owner.strip("'") not in (QUOTE_SHEET,'_layout_source'):
                            raise ValueError(f"予期しない外部参照: {token.value}")
                    endpoints=ref.split(':')
                    first, address=map_cell(endpoints[0])
                    if len(endpoints)==2:
                        last,finish=map_cell(endpoints[1])
                        if first!=last: raise ValueError('数式の範囲が印刷ページを跨いでいます。')
                        address+=':'+finish
                    token.value=address if first==name else f"'{first}'!{address}"
                cell.value='='+''.join(t.value for t in tokens)
    del wb[source.title]
    wb.active=0
    return blocks


def render_estimate_pdf(estimate):
    """Return a fixed A4 landscape PDF independent of Numbers print preferences."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.colors import HexColor, white
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from xml.sax.saxutils import escape
    from template_fill_test_v2 import (_build_detail_rows,_paginate_detail_rows,
        COMPANY,LOGO_PATH)
    font='HeiseiKakuGo-W5'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    output=BytesIO(); w,h=landscape(A4)
    c=canvas.Canvas(output,pagesize=(w,h)); c.setTitle(f'{estimate.project_name} 見積書')
    navy=HexColor('#1F3557'); rule=HexColor('#8A97AB'); tint=HexColor('#EDF1F6')
    x=22; width=w-44; top=h-24
    pages=_paginate_detail_rows(_build_detail_rows(estimate))
    total=2+len(pages)
    def text(value,xx,yy,size=9,color=navy,align='left'):
        c.setFillColor(color);c.setFont(font,size)
        draw={'left':c.drawString,'right':c.drawRightString,'center':c.drawCentredString}[align]
        draw(xx,yy,str(value))
    def band(label,yy,center=False):
        c.setFillColor(navy);c.rect(x,yy-24,width,24,fill=1,stroke=0)
        text(label,x+width/2 if center else x+12,yy-17,12,white,'center' if center else 'left')
    def footer(page):
        text(COMPANY['name']+'　'+estimate.project_name,x,15,7,HexColor('#808080'))
        text(f'{page} / {total}',w-22,15,7,HexColor('#808080'),'right')
    band('御　見　積　書',top,center=True)
    customer=re.sub(r'[\s　]*(御中|様|殿)[\s　]*$','',estimate.customer)
    suffix=re.search(r'(御中|様|殿)[\s　]*$',estimate.customer)
    honorific=suffix.group(1) if suffix else '御中'
    text(customer,x+175,top-75,17,align='center');text(honorific,x+385,top-75,12)
    c.setStrokeColor(navy); c.line(x,top-87,x+410,top-87)
    text('下記の通りお見積もり申し上げます。',x,top-113,9)
    if LOGO_PATH.exists():
        c.drawImage(ImageReader(str(LOGO_PATH)),w-330,top-77,width=130,height=47,mask='auto')
    text('見積日',w-330,top-113,9);text(estimate.issue_date,w-245,top-113,9)
    text('登録番号',w-330,top-132,9);text(COMPANY['reg'],w-245,top-132,9)
    c.setFillColor(navy);c.rect(x,top-224,205,43,fill=1,stroke=0)
    text('御 見 積 金 額',x+103,top-207,14,white,'center')
    c.setStrokeColor(navy);c.rect(x+205,top-224,255,43,fill=0,stroke=1)
    text(f'¥{estimate.grand_total:,}',x+450,top-210,22,align='right')
    text('（消費税込）',x+450,top-238,8,align='right')
    yy=174
    for label,value in [('工事名称',estimate.project_name),('工事場所',''),('工事期間',''),
            ('支払条件','ご相談の上'),('有効期限',estimate.valid_days),('見積担当','中村 哲也')]:
        text(label,x+120,yy,9);text(value,x+180,yy,9)
        c.setStrokeColor(rule);c.line(x+178,yy-4,x+442,yy-4);yy-=19
    text(COMPANY['name'],x+460,174,13)
    text(COMPANY['zip']+' '+COMPANY['address'],x+460,152,8)
    text(COMPANY['tel']+'　'+COMPANY['fax'],x+460,132,8)
    text(COMPANY['ceo'],x+460,112,9)
    footer(1);c.showPage()
    widths=[26,192,168,38,33,75,90,width-622]
    style=ParagraphStyle('cell',fontName=font,fontSize=9,leading=11,wordWrap='CJK',textColor=navy)
    def cells(values,yy,height=22,fill=None,bold=False):
        xx=x
        c.setStrokeColor(rule);c.setLineWidth(.45)
        for i,(value,cw) in enumerate(zip(values,widths)):
            if fill:
                c.setFillColor(fill);c.rect(xx,yy-height,cw,height,fill=1,stroke=0)
            c.rect(xx,yy-height,cw,height,fill=0,stroke=1)
            if value is not None and value!='':
                value=(f'{value:,.0f}' if float(value).is_integer() else f'{value:,.2f}') if isinstance(value,(int,float)) else str(value)
                if i in (3,5,6): text(value,xx+cw-4,yy-height/2-3,9,white if fill==navy else navy,'right')
                elif fill==navy: text(value,xx+cw/2,yy-height/2-3,9,white,'center')
                else:
                    para=Paragraph(escape(value),style);_,ph=para.wrap(cw-8,height)
                    if ph>height: raise ValueError(f'PDFの行に収まりません: {value}')
                    para.drawOn(c,xx+4,yy-height/2-ph/2)
            xx+=cw
        return yy-height
    def headers(label):
        band(label,top)
        return cells(['No.','工事品目','仕様','数量','単位','単価','金額','備考'],top-24,24,navy)
    yy=headers('工 事 品 目 ま と め')
    for i,cat in enumerate(estimate.categories,1):
        yy=cells([i,cat.name,'工事一式',1,'式','',cat.subtotal,''],yy,26)
    for _ in range(16-len(estimate.categories)): yy=cells(['']*8,yy,18)
    text('※工事内容明細には消費税が含まれておりません。',x,yy-21,8)
    sy=yy-34
    for label,amount in [('小計',estimate.subtotal),('値引き',estimate.discount),('税抜合計',estimate.net_total),('消費税',estimate.tax),('税込合計',estimate.grand_total)]:
        c.setFillColor(navy if label=='税込合計' else tint);c.rect(x+542,sy-19,105,19,fill=1,stroke=0)
        c.setStrokeColor(rule);c.rect(x+542,sy-19,105,19,fill=0,stroke=1);c.rect(x+647,sy-19,94,19,fill=0,stroke=1)
        text(label,x+594,sy-13,9,white if label=='税込合計' else navy,'center')
        text(('¥' if label=='税込合計' else '')+f'{amount:,}',x+737,sy-13,10,align='right');sy-=19
    footer(2);c.showPage()
    for page_no,rows in enumerate(pages,3):
        yy=headers('工 事 内 容 明 細'); amount=0
        for row in rows:
            kind=row['kind']
            if kind=='category': values=['',f"{row['index']}. {row['category'].name}",'','','','','',''];fill=tint
            elif kind=='subtotal': values=['','','','','','小計',row['category'].subtotal,''];fill=None
            else:
                item=row['item'];values=[row['no'],item.name,item.spec,item.qty,item.unit,item.unit_price,item.amount,item.remark];fill=None;amount+=item.amount
            yy=cells(values,yy,22,fill)
        for _ in range(20-len(rows)): yy=cells(['']*8,yy,18)
        text('※工事内容明細には消費税が含まれておりません。',x,yy-19,8)
        text('合　計',x+560,yy-43,11);text(f'{amount:,}',x+710,yy-43,11,align='right')
        footer(page_no);c.showPage()
    c.save();return output.getvalue()

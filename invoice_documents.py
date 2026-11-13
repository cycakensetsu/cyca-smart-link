"""Invoice documents derived from an immutable, approved estimate.

No storage, numbering allocation, email or network side effects. Callers must
reserve invoice_number uniquely and persist the reviewed fingerprint themselves.
"""
from copy import deepcopy
from datetime import date
from io import BytesIO
import re
import unicodedata
from xml.sax.saxutils import escape

from estimate_review import digest, verify_approval


def _text(value, field, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(field + 'を確認してください。')
    return value.strip()


def _date(value):
    if isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str):
        raise ValueError('日付を確認してください。')
    value = value.strip()
    match = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日', value)
    try:
        if match:
            return date(*map(int, match.groups())).isoformat()
        return date.fromisoformat(value.replace('/', '-')).isoformat()
    except ValueError as exc:
        raise ValueError('日付は西暦の年月日で指定してください。') from exc


def _money(value):
    if type(value) is not int or value < 0:
        raise ValueError('金額は0円以上の整数で確認してください。')
    return value


def build_invoice(estimate_approval, *, invoice_number, issue_date, due_date,
                  issuer, estimate_date=None):
    """Preserve the approved selling amounts, customer and project exactly.

    issuer: {name, details: [address, phone, registration number, ...],
             payment_details: [bank, branch, account holder, ...]}.
    Only owner-confirmed information should be supplied; nothing is inferred.
    """
    approval = deepcopy(estimate_approval)
    if not isinstance(approval, dict) or not isinstance(approval.get('payload'), dict):
        raise ValueError('承認済み見積書を選択してください。')
    payload = approval['payload']
    verify_approval({'payload': payload, 'fingerprint': digest(payload), 'unresolved': []}, approval)
    header = payload.get('表題', {})
    totals = payload.get('合計', {})
    try:
        net, tax, gross = (_money(totals[k]) for k in ('税抜売価', '税額', '税込売価'))
    except KeyError as exc:
        raise ValueError('元見積書の合計を確認してください。') from exc
    if net + tax != gross:
        raise ValueError('元見積書の税抜・税額・税込が一致しません。')
    source_date = _date(header.get('見積日') or estimate_date)
    # A supplied date may fill a legacy missing date, never silently replace one.
    if estimate_date is not None and _date(estimate_date) != source_date:
        raise ValueError('元見積書の日付と参照日が一致しません。')
    result = {'schema': 1, 'kind': 'invoice',
              'invoice_number': _text(invoice_number, '請求書番号', 64),
              'estimate_approval_id': approval['id'], 'estimate_fingerprint': approval['fingerprint'],
              'estimate_date': source_date,
              'customer': _text(header.get('宛名'), '宛名'),
              'project': _text(header.get('工事名称'), '工事名'),
              'issue_date': _date(issue_date), 'due_date': _date(due_date),
              'net': net, 'tax': tax, 'gross': gross,
              'tax_rate': header.get('顧客税率'), 'issuer': deepcopy(issuer)}
    validate_invoice(result)
    return result


def validate_invoice(invoice):
    if not isinstance(invoice, dict) or invoice.get('schema') != 1 or invoice.get('kind') != 'invoice':
        raise ValueError('請求書形式を確認してください。')
    for key in ('invoice_number', 'customer', 'project'):
        _text(invoice.get(key), key, 200 if key != 'invoice_number' else 64)
    for key in ('estimate_approval_id', 'estimate_fingerprint'):
        if not re.fullmatch('[a-f0-9]{64}', str(invoice.get(key, ''))):
            raise ValueError('元見積書の参照情報を確認してください。')
    for key in ('estimate_date', 'issue_date', 'due_date'):
        if _date(invoice.get(key)) != invoice[key]:
            raise ValueError('日付形式を確認してください。')
    if invoice['due_date'] < invoice['issue_date']:
        raise ValueError('支払期限は発行日以降にしてください。')
    if _money(invoice.get('net')) + _money(invoice.get('tax')) != _money(invoice.get('gross')):
        raise ValueError('請求金額が一致しません。')
    if type(invoice.get('tax_rate')) not in (int, float) or invoice['tax_rate'] not in (0, .08, .10):
        raise ValueError('税率を確認してください。')
    issuer = invoice.get('issuer')
    if not isinstance(issuer, dict):
        raise ValueError('発行者情報を確認してください。')
    _text(issuer.get('name'), '発行者名', 100)
    for key in ('details', 'payment_details'):
        lines = issuer.get(key, [])
        if not isinstance(lines, list) or len(lines) > 6:
            raise ValueError('発行者情報の行数を確認してください。')
        for line in lines:
            _text(line, '発行者情報', 100)


def invoice_fingerprint(invoice):
    validate_invoice(invoice)
    return digest(invoice)


def invoice_filename(invoice):
    validate_invoice(invoice)
    title = unicodedata.normalize('NFKC', invoice['project'])
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title).strip(' .')[:80] or '工事'
    return f"{title}請求書_{invoice['issue_date'].replace('-', '')}.pdf"


def _jp_date(value):
    d = date.fromisoformat(value)
    return f'{d.year}年{d.month}月{d.day}日'


def render_invoice_pdf(invoice, *, preview=True, approved_fingerprint=None):
    """Return PDF bytes. Final output requires exact current review fingerprint.

    A fingerprint is an integrity check, not authentication: authenticated calling
    services must enforce who can approve and persist the approval/audit record.
    """
    fingerprint = invoice_fingerprint(invoice)
    if not preview and approved_fingerprint != fingerprint:
        raise ValueError('請求書の最新内容を確認・承認してください。')
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle
    from reportlab.pdfgen.canvas import Canvas
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
    font = 'HeiseiKakuGo-W5'
    output = BytesIO()
    width, height = landscape(A4)
    canvas = Canvas(output, pagesize=(width, height))
    canvas.setTitle('請求書')
    canvas.setAuthor(invoice['issuer']['name'])
    def paragraph(text, x, y, w, *, size=9, align=0, max_height=40):
        style = ParagraphStyle('cell', fontName=font, fontSize=size, leading=size*1.4,
                               alignment=align, wordWrap='CJK')
        p = Paragraph(escape(str(text)), style)
        _, h = p.wrap(w, max_height)
        if h > max_height:
            raise ValueError('請求書の記載が表示領域を超えています。表記を短くしてください。')
        p.drawOn(canvas, x, y-h)
    # Landscape arrangement follows the user's existing Numbers invoice:
    # addressee/amount/due date left, work table/totals/bank details right.
    canvas.setStrokeColor(colors.HexColor('#333333'))
    canvas.setLineWidth(.6)
    canvas.rect(15, 15, width-30, height-30)
    paragraph('請 求 書', 35, height-30, 220, size=23)
    paragraph(_jp_date(invoice['issue_date']), 35, height-66, 260, size=12)
    paragraph('請求書番号：' + invoice['invoice_number'], 35, height-90, 320, size=8)
    if preview:
        paragraph('確認用・未確定', 35, height-111, 320, size=11)
    issuer = invoice['issuer']
    paragraph(issuer['name'], 390, height-35, 415, size=18)
    issuer_lines = issuer.get('details', [])
    for i, line in enumerate(issuer_lines):
        # Two columns preserve space without fabricating registration or seals.
        paragraph(line, 390+(i%2)*210, height-68-(i//2)*18, 202, size=8, max_height=17)
    paragraph(invoice['customer'], 35, height-150, 310, size=12, max_height=50)
    canvas.line(35, height-203, 350, height-203)
    paragraph('下記のとおりご請求申し上げます。', 35, height-218, 310, size=9)
    paragraph('合計金額（消費税込）', 35, 265, 190, size=10)
    paragraph(f"¥ {invoice['gross']:,}", 210, 269, 140, size=17, align=2)
    canvas.setLineWidth(1.2)
    canvas.line(35, 242, 350, 242)
    canvas.setLineWidth(.6)
    canvas.rect(35, 177, 315, 47)
    paragraph('下記の期日までにお振込みをお願いいたします。', 45, 215, 295, size=8)
    paragraph('お支払期限：' + _jp_date(invoice['due_date']), 45, 196, 295, size=11)
    paragraph('（施工内容明細）', 390, 448, 415, size=10)
    cell_style = ParagraphStyle('table', fontName=font, fontSize=8, leading=10, wordWrap='CJK')
    def cell(value): return Paragraph(escape(str(value)), cell_style)
    reference = _jp_date(invoice['estimate_date']) + '付 別見積書のとおり'
    data = [[cell('工事名称'), cell('数量'), cell('単位'), cell('単価'), cell('金額')],
            [cell(invoice['project']), cell('1'), cell('式'), '', cell(f"{invoice['net']:,}")]]
    data += [['', '', '', '', ''] for _ in range(5)]
    data += [[cell(reference), '', '', '', '']]
    data += [['', '', '', '', ''] for _ in range(3)]
    table = Table(data, colWidths=[210, 35, 35, 55, 90], rowHeights=[22]+[20]*10)
    # Reject text that cannot fit, rather than silently clipping a client's name.
    for value in (invoice['project'], reference):
        if cell(value).wrap(198, 18)[1] > 18:
            raise ValueError('工事名または見積参照が長いため、請求書の表記を確認してください。')
    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.black),
                              ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dddddd')),
                              ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                              ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
    table.wrapOn(canvas,425,230); table.drawOn(canvas,390,205)
    totals = Table([['小計', f"¥ {invoice['net']:,}"],
                    [f"消費税 {invoice['tax_rate']*100:g}%", f"¥ {invoice['tax']:,}"],
                    ['合計', f"¥ {invoice['gross']:,}"]], colWidths=[100, 115], rowHeights=19)
    totals.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),font),('FONTSIZE',(0,0),(-1,-1),9),
                               ('GRID',(0,0),(-1,-1),.5,colors.black),
                               ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#dddddd')),
                               ('ALIGN',(1,0),(1,-1),'RIGHT'),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    totals.wrapOn(canvas,215,60);totals.drawOn(canvas,600,130)
    payment = issuer.get('payment_details', [])
    if payment:
        canvas.rect(390, 30, 425, 85)
        paragraph('振込先口座', 400, 108, 405, size=11)
        for i,line in enumerate(payment):
            paragraph(line,400,89-i*10,405,size=7,max_height=10)
    canvas.showPage()
    canvas.save()
    return output.getvalue()

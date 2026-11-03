"""Bind a manual source comparison to the exact scanned reading being reviewed."""
import hashlib
import json

_REASONS = {
    'invalid_source': '原本との対応を確認できません。',
    'missing_row_evidence': '品目ごとの仕様・注記を独立して照合した記録がありません。',
    'incomplete_row_evidence': '品目ごとの仕様・注記の照合が終わっていません。',
    'invalid_row_location': '原本の品名またはページが未確認です。',
    'ambiguous_row': '同じ品名があり、注記がどの行に属するか確定できません。',
    'missing_row': '別の読み取りに、この品目の照合記録がありません。',
    'field_difference': '仕様または注記の読み取りが食い違っています。',
    'unmatched_evidence': '別の読み取りにだけ現れる品目があります。',
    'missing_current_rows': '今回の品目と原本の照合記録が対応していません。',
}


def requirement(rows, source):
    # Low-level callers can supply an already manually prepared ledger without
    # an automated reading. The public source-review route always attaches its
    # actual amount_reading before invoking this gate. Every automated reading,
    # including old sessions, empty metadata, native PDFs and images, must have
    # row evidence or a source-bound original comparison reason.
    if 'amount_reading' not in source:
        return None
    from row_evidence_review import compare_row_evidence
    result = compare_row_evidence(rows, source)
    if result['status'] == 'completed':
        return None
    facts = [{k: r.get(k) for k in ('行ID', '原本ID', 'ページ', '品名', '仕様', '備考',
              '部位', '数量', '単位', '原価単価', '原価金額', '採用', '訂正理由')} for r in rows]
    payload = {'source_id': source.get('id'), 'rows': facts, 'result': result}
    signature = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), default=str).encode()).hexdigest()
    return {'signature': signature, 'result': result}


def verified(rows, source):
    needed = requirement(rows, source)
    if needed is None:
        return True
    answer = source.get('row_evidence_confirmation', {})
    return (isinstance(answer, dict) and answer.get('signature') == needed['signature']
            and isinstance(answer.get('reason'), str) and bool(answer['reason'].strip()))


def render(st, rows, source):
    needed = requirement(rows, source)
    if needed is None:
        source.pop('row_evidence_confirmation', None)
        return True
    st.warning('金額が合っていても、仕様・注記の対応は未確認です。左の原本で該当箇所を見比べてください。')
    with st.expander('仕様・注記の読み取りを見比べる', expanded=True):
        for item in needed['result']['findings']:
            label = ('原本' if not item.get('name') else str(item['name']))
            if item.get('page'):
                label += '（' + str(item['page']) + 'ページ）'
            st.text(label + '：' + _REASONS.get(item.get('reason'), '対応を確認してください。'))
            for field in item.get('fields', []):
                st.text(field + ' ／ 今回：' + str(item.get('actual', {}).get(field, '')))
                st.text(field + ' ／ 別の読み取り：' + str(item.get('expected', {}).get(field, '')))
        st.caption('違っていれば上の訂正欄で直してください。別の読み取りも正解とは限りません。原本を見て採用した表記と理由を残します。')
        reason = st.text_input('原本で仕様・注記を確認した内容と理由',
                              key='review_row_evidence_' + needed['signature'])
    source['row_evidence_confirmation'] = {'signature': needed['signature'], 'reason': reason}
    return verified(rows, source)

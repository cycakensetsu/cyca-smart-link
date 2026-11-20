"""Display original subtotal evidence and ask for an internal-only price decision."""
from estimate_review import digest, source_provisional_eligible, source_subtotal_manual_eligible
from source_subtotal_checks import manual_clean_candidate


def _yen(value):
    return f'{value:,}円' if type(value) is int else '未確認'


def _location(check):
    page = check.get('page')
    return f'{page}ページ目' if type(page) is int and page > 0 else 'ページ不明'


def render(st, source, rows, actual, *, other_blockers):
    """Keep printed amounts untouched; persist only the explicit UI decision."""
    checks = source.get('printed_subtotal_checks', [])
    source.pop('source_variance_decision', None)
    source.pop('source_subtotal_manual_decision', None)
    issues = [c for c in checks if c.get('status') in ('variance', 'unverified')]
    if not issues:
        return False
    st.subheader('見積書の小計を確認してください', anchor=False)
    unknown = False
    can_confirm_candidate = all(manual_clean_candidate(check) for check in issues)
    for check in issues:
        location = _location(check)
        printed, computed, difference = (check.get(k) for k in ('printed_subtotal', 'computed_subtotal', 'difference'))
        if check['status'] == 'unverified':
            unknown = True
            if can_confirm_candidate:
                st.info(f'{location}：読み取った小計と明細の合計は {_yen(printed)} です。原本で、全品目と小計を見比べてください。')
            else:
                st.warning(f'{location}：小計と品目の対応を確認できません。左の原本で、抜けや重複がないか確認してください。')
                if printed is not None:
                    st.caption(f'見積書に書かれた小計：{_yen(printed)}')
        else:
            st.warning(f'{location}：明細の合計 {_yen(computed)} ／ 見積書の小計 {_yen(printed)} ／ 差 {_yen(abs(difference) if type(difference) is int else None)}')
    if unknown:
        manual = None if other_blockers or not can_confirm_candidate else source_subtotal_manual_eligible(source, rows)
        if manual is None:
            return True
        cover = source.get('net_total')
        st.caption(f'原本に記載された税抜金額：{_yen(cover)} ／ 明細から計算した税抜金額：{_yen(actual)}')
        if st.checkbox('原本の小計と全品目を見比べました', key='review_subtotal_manual_' + manual['fingerprint']):
            source['source_subtotal_manual_decision'] = manual
            return False
        return True

    cover = source.get('net_total')
    if type(cover) is int and type(actual) is int:
        st.caption(f'原本に記載された税抜金額：{_yen(cover)} ／ 明細から計算した税抜金額：{_yen(actual)}')
    decision = None if other_blockers else source_provisional_eligible(source, rows)
    if decision is None:
        st.info('仮の金額を決める前に、原本の品目と合計を確認してください。')
        return True

    fingerprint = decision['fingerprint']
    # Binding the widget to the exact source and evidence prevents a stale
    # selection being carried over after working rows or evidence are changed.
    widget_key = 'review_subtotal_provisional_' + digest([fingerprint, rows])
    st.write(f'高い方の{_yen(actual)}（税抜）で仮見積を作りますか？')
    if st.checkbox(f'{_yen(actual)}で仮見積を作る', key=widget_key):
        source['source_variance_decision'] = decision
        st.info('社内確認用です。業者に確認するまでは、提出用の見積書を確定・保存できません。')
    return True

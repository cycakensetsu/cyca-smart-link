"""Explicit, persisted selection; cleared in both live and retained state."""
from pricing_options import choice_for, selected, input_context

KEY = 'review_pricing_choice'


def set_choice(state, value):
    if value is None:
        state.pop(KEY, None)
        state.get('_work_draft_inputs', {}).pop(KEY, None)
    else:
        state[KEY] = value
        # Also replace a retained choice so clearing/rejecting cannot revive it.
        if '_work_draft_inputs' in state:
            state['_work_draft_inputs'][KEY] = dict(value)


def reconcile_choice(state, options):
    choice = state.get(KEY)
    if choice:
        try:
            selected(options, choice)
        except ValueError:
            set_choice(state, None)
            return True
    return False


def reconcile_inputs(state, rows, targets, tax_rate, requested_discount, rounding=None):
    """Clear even invalid edits before validation, so undo cannot revive consent."""
    choice = state.get(KEY)
    if not choice:
        return False
    try:
        current = input_context([r for r in rows if r.get('採用',True)], targets, tax_rate, requested_discount, rounding)
        matches = choice.get('context_id') == current
    except (ValueError, TypeError, KeyError):
        matches = False
    if not matches:
        set_choice(state, None)
        return True
    return False


def render_options(st, options):
    if not options:
        return
    if reconcile_choice(st.session_state, options):
        st.info('金額条件が変わったため、値引き案の採用を解除しました。')
    if options['no_discount'] and not options['proposal'] and not options['proposal_error'] and not options.get('requires_confirmation'):
        st.caption('値引きなしで指定利益に合わせています。値引き行は出ません。')
        return
    st.markdown('#### 単価の整え方を選ぶ')
    st.caption('通常は値引きなしです。案を見るだけでは適用されません。最終利益は指定した金額を守ります。')
    if options.get('requires_confirmation'):
        st.warning('端数単価または利益の偏りがあります。案を選ぶまで確定・出力しません。')
        for warning in options.get('warnings',[]):
            st.caption(warning)
    alternatives = []
    if options['no_discount']:
        alternatives.append({'案':'値引きなし', **options['no_discount']['totals']})
    else:
        st.warning('値引きなしでは指定利益に合わせられません：' + options['no_discount_error'])
    proposal = options['proposal']
    if proposal:
        alternatives.append({'案':'単価を整えて値引き調整', **proposal['totals']})
    if alternatives:
        import pandas as pd
        comparison = pd.DataFrame(alternatives).set_index('案').drop(columns=['仕入原価']).T
        for column in comparison:
            comparison[column] = comparison[column].map(lambda value: f'{value:,}円')
        st.dataframe(comparison.rename_axis('比較項目').reset_index(), hide_index=True, width='stretch')
    if proposal:
        st.caption('単価を整える案では通常の配分上限を超える場合があります。各単価と値引き額を確認して選んでください。')
        import pandas as pd
        st.dataframe(pd.DataFrame(proposal['rows'])[[c for c in ('品名','数量','単位','原価単価','見積単価','見積金額') if c in proposal['rows'][0]]], hide_index=True, width='stretch')
    if options['proposal_error']:
        st.caption(options['proposal_error'])
    st.caption('指定利益と、見積総額をキリのよい金額にすることは別です。原価・指定利益は変更していません。')
    choice = st.session_state.get(KEY)
    adopted = choice and choice['mode'] == 'discount_adjustment'
    if proposal and not adopted:
        if st.button('この値引き案を採用', key='review_adopt_discount'):
            set_choice(st.session_state, choice_for(options, True))
            st.rerun()
    if adopted:
        st.success('選んだ値引き案を反映しています。値引き後の利益を確認してください。')
    if proposal or adopted or options.get('requires_confirmation'):
        if st.button('値引きなしにする', disabled=not bool(options['no_discount']), key='review_reject_discount'):
            set_choice(st.session_state, choice_for(options, False))
            st.rerun()

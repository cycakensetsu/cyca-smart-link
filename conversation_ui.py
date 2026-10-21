"""Conversation proposes edits to the existing approval form; never approves it."""
import copy
from dataclasses import asdict

from estimate_review import digest
from conversation_assistant import apply_proposal, converse, transcribe, comparison_figures, print_rows


def widget_fingerprint(state):
    prefixes = ('review_rows_', 'review_target_', 'review_basis_', 'review_tax_',
                'review_net_', 'review_gross_', 'review_source_ok_')
    names = {'review_scope', 'review_target_mode', 'review_discount', 'review_discount_reason',
             'review_customer', 'review_project', 'review_date', 'review_customer_tax', 'review_page_breaks'}
    return digest({key: state[key] for key in state if key in names or key.startswith(prefixes)})


def apply_pending(st, store):
    pending = st.session_state.pop('_review_assistant_apply', None)
    if not pending:
        return
    try:
        if pending['widgets'] != widget_fingerprint(st.session_state):
            raise ValueError('入力が変わりました。')
        pending = pending['proposal']
        before = st.session_state['_review_working']
        after = apply_proposal(pending, before)
        record = {'kind': 'conversation_application',
                  'extraction_id': st.session_state.get('_review_extraction_id', ''),
                  'history': st.session_state.get('_review_chat', []),
                  'proposal': pending, 'before': digest(before), 'after': digest(after)}
        record_id = store.snapshot('extractions', record)
        # These are set before any corresponding input widgets are instantiated.
        st.session_state['_review_assistant_rows'] = after['rows']
        st.session_state['_review_editor_epoch'] = st.session_state.get('_review_editor_epoch', 0) + 1
        mapping = {'宛名': 'review_customer', '工事名称': 'review_project', '見積日': 'review_date',
                   '値引き理由': 'review_discount_reason'}
        for field, key in mapping.items():
            st.session_state[key] = after['metadata'][field]
        st.session_state['review_discount'] = after['discount']
        st.session_state['review_page_breaks'] = after['metadata'].get('改ページ', [])
        st.session_state['_review_requested_pages'] = after['metadata'].get('希望ページ', {})
        targets = after['targets']
        if targets:
            st.session_state['review_scope'] = targets[0]['scope']
            st.session_state['review_target_mode'] = targets[0]['mode']
        for key in list(st.session_state):
            if key.startswith(('review_target_', 'review_agree_')):
                del st.session_state[key]
        for target in targets:
            st.session_state['review_target_' + digest([target['scope'], target['mode'], target['key']])] = float(target['value'])
        for key in ('_review_result', '_review_approval', '_review_preview', '_vault_archive', '_review_proposal'):
            st.session_state.pop(key, None)
        st.session_state['_review_conversation_record'] = record_id
        st.session_state['_review_assistant_notice'] = '案を入力欄に反映しました。下の金額と確認事項を見てから承認してください。'
    except (ValueError, OSError, KeyError):
        st.session_state['_review_assistant_notice'] = '入力が変わったか記録を保存できませんでした。最新の内容でもう一度相談してください。'


def working_context(rows, sources, targets, discount, metadata):
    return copy.deepcopy({'rows': rows, 'sources': sources,
        'targets': [asdict(t) for t in targets], 'discount': discount, 'metadata': metadata, 'print_rows': print_rows(rows)})


def change_description(c, context):
    if c['kind'] == 'metadata':
        return f"{c['field']} → {c['value']}"
    if c['kind'] == 'target':
        return f"{c['scope']}・{c['key']}：{c['mode']} → {c['value']:g}" + ('円' if c['mode'] == '上乗せ額' else '%' if c['mode'] == '粗利益率' else '倍')
    if c['kind'] == 'discount':
        return f"顧客値引き → {c['value']:,}円（{c['reason']}）"
    if c['kind'] == 'autopages':
        return 'ページ分け → 自動で見やすく分け直す'
    if c['kind'] == 'pagebreak':
        row = next(r for r in context['print_rows'] if r['row_id'] == c['row_id'])
        return f"No.{row['no']} {row['name']}から新しいページ" + (f"（希望：{c['value']}ページ目）" if c['value'] else '')
    row = next(r for r in context['rows'] if r['行ID'] == c['row_id'])
    value = ('採用する' if c['value'] else '除外する') if c['field'] == '採用' else c['value']
    return f"{row['見積元']}・{row['品名']}：{c['field']} → {value}" + (f"（{c['reason']}）" if c['reason'] else '')


def render_conversation(st, api_key, context):
    st.session_state['_review_working'] = context
    st.markdown('### 2. AIと見積プランをつくる')
    st.caption('話しかけるだけで、価格・利益・値引き・ページ分けを一緒に整理できます。例：「利益を10万円確保したい」「防水工事の粗利益率を20%にして」「足場は別ページにして」。提案を確認してから反映します。')
    checked = bool(context['sources']) and all(s.get('confirmed') for s in context['sources'])
    ready = bool(api_key) and checked
    if not checked:
        st.info('先にステップ1で、元の見積書と読み取り結果を確認してください。確認できた原価をもとに、見積プランをつくります。')
    if checked:
        figures = comparison_figures(context)
        if figures:
            with st.expander('価格と利益のバランスを見る'):
                st.caption('比較のための計算例です。値引き後の粗利益額も表示します。適正利益の推奨値ではありません。')
                st.dataframe(figures, hide_index=True, width='stretch')
    notice = st.session_state.pop('_review_assistant_notice', '')
    if notice:
        st.info(notice)
    history = st.session_state.get('_review_chat', [])
    if history:
        with st.expander('これまでの相談', expanded=True):
            for message in history:
                with st.chat_message(message['role']):
                    st.write(message['content'])
    st.caption('「文字にする」「相談する」を押すと、音声または相談文と現在の見積内容を、読み取りに使っているAIへ送ります。録音そのものは保管しません。反映した相談内容は社内用バックアップに含まれます。')
    audio = st.audio_input('マイクを押して今回の要望を話す（2分以内）', key='review_voice_' + str(st.session_state.get('_review_editor_epoch', 0)), disabled=not ready)
    if st.button('録音を文字にする', key='review_transcribe', disabled=audio is None or not ready):
        try:
            with st.spinner('話した内容を文字にしています…'):
                words = transcribe(api_key, audio.getvalue(), st.secrets)
            st.session_state['review_instruction'] = words
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error('音声を読み取れませんでした。録り直すか、文章で入力してください。')
    instruction = st.text_area('今回の要望（聞き取った文章はここで直せます）',
        key='review_instruction', max_chars=4000,
        placeholder='例：この型で作って。全体に10万円上乗せして。値引きは1万円、理由は契約調整。')
    if not api_key:
        st.info('音声・会話の利用には、既存のAI接続設定が必要です。')
    if st.button('この内容で相談する', key='review_consult', disabled=not ready or not instruction.strip()):
        try:
            with st.spinner('ご要望を整理して変更案を作っています…'):
                updated, proposal = converse(api_key, instruction, history, context, st.secrets)
            st.session_state['_review_chat'] = updated
            st.session_state['_review_proposal'] = proposal
            st.session_state['_review_proposal_widgets'] = widget_fingerprint(st.session_state)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error('相談を読み取れませんでした。入力を短くして再送してください。')
    proposal = st.session_state.get('_review_proposal')
    if proposal and proposal['changes']:
        st.write('入力欄に反映する案')
        for change in proposal['changes']:
            st.write('・' + change_description(change, context))
        stale = proposal['base'] != digest(context)
        if stale:
            st.warning('相談後に入力が変わっています。最新の内容でもう一度相談してください。')
        if any(c['kind'] == 'target' for c in proposal['changes']):
            st.caption('指定範囲・方法が変わる場合、以前の範囲・方法の目標は置き換えます。同じ範囲・方法の他の対象は保持します。')
        if st.button('この案を入力欄に反映', key='review_apply_conversation', disabled=stale or not ready):
            st.session_state['_review_assistant_apply'] = {
                'proposal': proposal, 'widgets': st.session_state['_review_proposal_widgets']}
            st.rerun()
    if st.button('相談を新しくする（入力済みの見積は保持）', key='review_new_conversation'):
        for key in ('_review_chat', '_review_proposal', '_review_proposal_widgets', '_review_assistant_apply'):
            st.session_state.pop(key, None)
        st.session_state['_review_clear_instruction'] = True
        st.rerun()

"""Shared Streamlit UI for both entrypoints; all data stays in the user's vault."""
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import copy
import os

import pandas as pd
from work_effects import cue, confirmed_sources, download_cue

from estimate_review import (OriginalVault, Target, REVIEW_MODE, review_rows, build_draft,
                             approve, build_approved_estimate, canonical, clean, digest)


def vault(st):
    root = os.environ.get("CYCA_DATA_DIR", str(Path(__file__).parent / ".private_data"))
    return OriginalVault(root, st.session_state.get("username", ""))


def render_archive(st):
    from vault_archive import restore_archive
    if not st.session_state.get('_review_export_ready'):
        return
    with st.expander("原本・記録もまとめて保存する（社内用・必要なときだけ）", expanded=False):
        st.caption("原本・作成済みの見積・記録を、PCにまとめて保存できます。社内用なのでお客様には送りません。")
        if not os.environ.get('CYCA_DATA_DIR'):
            st.caption("完成した見積と、読み取り・承認の記録もまとめて残す場合に使います。")
        try:
            render_backup_download(st, 'vault', auto=True)
            with st.expander('以前保存したデータを戻すときだけ開く'):
                upload = st.file_uploader("保存した作業データ（ZIP）", type=['zip'], key='vault_restore_file')
                if st.button("このデータを戻す", disabled=upload is None, key='vault_restore'):
                    count = restore_archive(vault(st), bytes(upload.getbuffer()))
                    st.session_state.pop('_vault_archive', None)
                    st.success(f"{count}件の保存データを戻しました。")
        except (ValueError, OSError) as exc:
            st.error(str(exc))


def render_backup_download(st, key, auto=False):
    from vault_archive import archive_bytes
    if (auto and '_vault_archive' not in st.session_state) or (not auto and st.button('最新の社内用バックアップを準備', key=key+'_prepare_archive')):
        data = archive_bytes(vault(st))
        if data:
            st.session_state['_vault_archive'] = (data, datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds'))
        else:
            st.session_state['_vault_archive'] = None
            st.caption('見積を取り込むと、ここから保存できるようになります。')
    prepared = st.session_state.get('_vault_archive')
    if prepared:
        st.caption('この時点までのデータ：' + prepared[1])
        st.download_button('作業データをまとめて保存', prepared[0],
                           file_name='サイカスマートリンク_社内バックアップ.zip', key=key+'_archive_download')


def preserve_uploads(st, uploads):
    st.session_state.pop('_vault_archive', None)
    store = vault(st)
    sources = []
    prepared = []
    by_id = {}
    used_names = set()
    for upload in uploads:
        source = store.preserve(upload.name, upload.getbuffer())
        if source['id'] in by_id:
            first=by_id[source['id']]
            first['duplicate_count'] = first.get('duplicate_count',1)+1
            first.setdefault('duplicate_names',[first.get('original_name',first['name'])]).append(upload.name)
            continue
        source['original_name']=upload.name
        if source['name'] in used_names:
            suffix=Path(upload.name).suffix
            stem=upload.name[:-len(suffix)] if suffix else upload.name
            number=2
            while f'{stem}（同名{number}）{suffix}' in used_names:number+=1
            source['name']=f'{stem}（同名{number}）{suffix}'
        used_names.add(source['name']);by_id[source['id']]=source
        if Path(source['name']).suffix.lower() == '.pdf':
            from pdf_worker import page_count
            try:source['pages']=page_count(bytes(upload.getbuffer()))
            except Exception:
                source['pages']=1
                source['prepare_error']=True
        else:source['pages']=1
        sources.append(source);prepared.append((source,upload))
    st.session_state['_review_sources']=sources
    return prepared


def capture_extraction(st, detail, summaries, records, read_errors=()):
    for key in list(st.session_state):
        if key.startswith('_review_') and key != '_review_sources':
            del st.session_state[key]
    st.session_state.pop('_vault_archive', None)
    sources = st.session_state.get("_review_sources", [])
    for source in sources:
        source['read_errors'] = [e for e in read_errors if e['source'] == source['name']]
    rows = review_rows(detail, sources)
    summary_only = {(r.get('__source_name'),r.get('__page_number'),str(r.get('品名')),r.get('金額')) for r in records if r.get('__summary_only')}
    for r in rows:
        r["集計のみ"] = (r['元ファイル'],r['ページ'],str(r['品名']),r['原価金額']) in summary_only
    raw = {"rows": rows, "summary": summaries, "sources": sources,
           "records": [{str(k): clean(v) for k, v in r.items()} for r in records]}
    extraction_id = vault(st).snapshot("extractions", raw)
    st.session_state["_review_original_rows"] = rows
    st.session_state["_review_metadata"] = dict(summaries or {})
    st.session_state["_review_extraction_id"] = extraction_id
    for key in list(st.session_state):
        if key.startswith("review_") or key in ("_review_approval", "_review_result"):
            del st.session_state[key]


def render_review(st, api_key=None):
    st.session_state['_review_export_ready'] = False
    original = st.session_state.get("_review_original_rows")
    if not original:
        return
    from conversation_ui import apply_pending, working_context, render_conversation
    apply_pending(st, vault(st))
    if st.session_state.pop('_review_clear_instruction', False):
        st.session_state['review_instruction'] = ''
    from review_input_ui import render_inputs
    sources=copy.deepcopy(st.session_state['_review_sources'])
    rows,sources=render_inputs(st,original,sources,vault(st))
    ready=bool(sources) and all(source.get('confirmed') for source in sources)
    if ready:
        _render_review_plan(st,api_key,original,rows,sources)


def customer_default(metadata):
    """A supplier's addressee is often CYCA itself, not the final customer."""
    import re,unicodedata
    value=str(metadata.get('宛名') or '')
    compact=re.sub(r'株式会社|有限会社|\(株\)|\(有\)|御中|様|殿|[\s㈱()（）]','',unicodedata.normalize('NFKC',value))
    return '' if compact=='彩架建設' else value


def _render_review_plan(st,api_key,original,rows,sources):
    from conversation_ui import working_context, render_conversation
    conversation_slot = st.container()
    st.markdown('### 3. 見積金額を確認する')
    st.caption('出力先：アプリ既存の横型流し込みデザイン。通常は工種ごとに明細を分け、2行程度の小工種は同じページにまとめます。少量の1社見積は一式まとめを省きます。')
    m = st.session_state.get('_review_metadata', {})
    with st.container(border=True):
        st.caption('見積書を渡すお客様と、表紙の工事名を確認してください。')
        customer = st.text_input('お客様名', value=customer_default(m), placeholder='例：〇〇株式会社 御中', key='review_customer')
        project = st.text_input('工事名', value=str(m.get('工事名称') or ''), key='review_project')
    with st.expander("金額・利益を手動で調整する", expanded=False):
        scope = st.radio("目標の指定範囲", ["全体", "業者別", "工種別"], horizontal=True, key="review_scope")
        mode = st.selectbox("目標の指定方法", ["上乗せ額", "掛け率", "粗利益率"], key="review_target_mode")
        accepted = [r for r in rows if r["採用"]]
        group = "見積元" if scope == "業者別" else "工事種別"
        keys = ["全体"] if scope == "全体" else list(dict.fromkeys(str(r[group]) for r in accepted))
        targets = []
        for key in keys:
            defaults = (0.0, 0.0, 10000.0) if mode == "上乗せ額" else ((1.0, 1.0, .05) if mode == "掛け率" else (0.0, 0.0, 1.0))
            value = st.number_input(f"{key}：{mode}" + ("（%）" if mode == "粗利益率" else ""),
                                    value=defaults[0], min_value=defaults[1], step=defaults[2],
                                    key="review_target_"+digest([scope,mode,key]))
            targets.append(Target(scope, key, mode, value))
        discount = st.number_input("顧客向け値引き（0円以下）", max_value=0, value=0, step=1000, key="review_discount")
        reason = st.text_input("顧客値引きの理由", key="review_discount_reason")
        st.caption("仕入側の値引きは原本明細の負数行として保持します。顧客値引きとは別に計算します。")
        metadata = {
            "宛名": customer,
            "工事名称": project,
            "見積日": st.text_input("見積日", value=datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d"), key="review_date"),
            "有効期限": "30日", "値引き理由": reason,
            "顧客税率": st.selectbox("顧客向け税率", [.10, .08, 0.0], format_func=lambda x:f"{x:.0%}", key="review_customer_tax"),
        }
    if st.session_state.get('_review_conversation_record'):
        metadata['相談反映記録'] = st.session_state['_review_conversation_record']
    from output_preview_ui import page_controls, render_preview
    metadata = page_controls(st, rows, metadata)
    with conversation_slot:
        render_conversation(st, api_key, working_context(rows, sources, targets, discount, metadata))
    resolutions = {}
    try:
        draft = build_draft(original, rows, sources, targets, discount, metadata)
        from review_findings_ui import render_findings
        resolutions=render_findings(st,draft,all(source.get('confirmed') for source in sources))
        draft = build_draft(original, rows, sources, targets, discount, metadata, resolutions)
    except (ValueError, TypeError, KeyError) as exc:
        st.error(str(exc))
        return
    totals = draft["payload"]["合計"]
    st.text('宛名：' + metadata['宛名'] + '　／　工事名称：' + metadata['工事名称'])
    st.metric('仕入原価（税抜）', f"{totals['仕入原価']:,}円")
    st.metric('利益（値引き前）', f"{totals['売価小計'] - totals['仕入原価']:,}円")
    st.metric('お客様への税込見積額', f"{totals['税込売価']:,}円")
    if discount:
        st.caption(f"顧客値引き：{discount:,}円 ／ 値引き後の粗利益：{totals['粗利益']:,}円")
    with st.expander('原価・売価・税・粗利益の内訳を確認'):
        st.dataframe(pd.DataFrame([totals]), hide_index=True, width="stretch")
    with st.expander('品目ごとの金額・工種別の内訳を見る'):
        result_rows = pd.DataFrame(draft["payload"]["計算明細"])
        st.dataframe(result_rows[[c for c in ("品名", "数量", "単位", "原価単価", "原価金額", "見積単価", "見積金額", "上乗せ額", "見積元", "工事種別") if c in result_rows]], hide_index=True, width="stretch")
        if not result_rows.empty and "見積金額" in result_rows:
            for col in ("見積元", "工事種別"):
                st.write(col + "ごとの原価・売価")
                st.dataframe(result_rows.groupby(col, sort=False)[["原価金額", "見積金額", "上乗せ額"]].sum(), width="stretch")
    valid = not draft["unresolved"] and bool(metadata["宛名"].strip()) and bool(metadata["工事名称"].strip())
    missing_names=[label for field,label in [('宛名','お客様名'),('工事名称','工事名')] if not metadata[field].strip()]
    if missing_names:
        st.info('・'.join(missing_names)+'を入れると、完成イメージを見られます。')
    from estimate_review import preview_allowed
    preview_ready = render_preview(st, draft, preview_allowed(draft) and not missing_names)
    agree = st.checkbox("内容とページ分けは、これでOKです", disabled=not preview_ready,
                        key="review_agree_"+draft["fingerprint"])
    if st.button("この内容で見積書を作成", disabled=not (valid and agree and preview_ready), key="review_approve"):
        try:
            approval = approve(draft, st.session_state["username"])
            result = build_approved_estimate(draft, approval)
            if not result.ok:
                st.error("帳票の検証に失敗しました。" + " / ".join(result.errors))
                return
            store = vault(st)
            store.snapshot("approvals", approval)
            store.export(approval, "xlsx", result.data)
            store.export(approval, "pdf", result.pdf_data)
            st.session_state.pop('_vault_archive', None)
            st.session_state["_review_result"] = result
            st.session_state["_review_approval"] = approval
            cue(st, 'output')
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    approval = st.session_state.get("_review_approval")
    result = st.session_state.get("_review_result")
    if approval and result and approval["fingerprint"] == draft["fingerprint"] and valid and agree and preview_ready:
        st.session_state['_review_export_ready'] = True
        st.success("見積書ができました。下のボタンから保存できます。")
        st.download_button("PDFを保存", result.pdf_data, file_name=result.file_name.removesuffix('.xlsx')+'.pdf', key="review_pdf", on_click=download_cue)
        st.download_button("Numbers用を保存", result.data, file_name=result.file_name, key="review_xlsx", on_click=download_cue)
        with st.expander('承認記録を見る・保存する（社内用）'):
            st.download_button("承認記録を保存", canonical(approval).encode(), file_name="社内承認記録.json", key="review_receipt")
    elif result:
        st.info("内容が変わりました。完成イメージをもう一度確認して、見積書を作り直してください。")

"""Shared Streamlit UI for both entrypoints; all data stays in the user's vault."""
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import copy
import os

import pandas as pd

from estimate_review import (OriginalVault, Target, REVIEW_MODE, review_rows, build_draft,
                             approve, build_approved_estimate, canonical, clean, digest)


def vault(st):
    root = os.environ.get("CYCA_DATA_DIR", str(Path(__file__).parent / ".private_data"))
    return OriginalVault(root, st.session_state.get("username", ""))


def render_archive(st):
    from vault_archive import restore_archive
    with st.sidebar.expander("原本・承認記録のバックアップ"):
        st.caption("社内用です。原本、仕入原価、顧客情報を含むため、お客様へ送らず会社の安全な場所に保管してください。")
        if not os.environ.get('CYCA_DATA_DIR'):
            st.warning("アプリ内の保存は一時的です。作業終了時に一括保存してください。再起動後はバックアップから復元できます。")
        try:
            render_backup_download(st, 'vault')
            upload = st.file_uploader("以前の社内用バックアップを復元", type=['zip'], key='vault_restore_file')
            if st.button("確認して復元", disabled=upload is None, key='vault_restore'):
                count = restore_archive(vault(st), bytes(upload.getbuffer()))
                st.session_state.pop('_vault_archive', None)
                st.success(f"{count}件を確認して復元しました。保存済みの同一データはそのまま保持します。")
        except (ValueError, OSError) as exc:
            st.error(str(exc))


def render_backup_download(st, key):
    from vault_archive import archive_bytes
    if st.button('最新の社内用バックアップを準備', key=key+'_prepare_archive'):
        data = archive_bytes(vault(st))
        if data:
            st.session_state['_vault_archive'] = (data, datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds'))
        else:
            st.info('保管データはまだありません。')
    prepared = st.session_state.get('_vault_archive')
    if prepared:
        st.caption('この時点までのデータ：' + prepared[1])
        st.download_button('原本・記録・帳票を一括保存（社内用ZIP）', prepared[0],
                           file_name='サイカスマートリンク_社内バックアップ.zip', key=key+'_archive_download')


def preserve_uploads(st, uploads):
    st.session_state.pop('_vault_archive', None)
    store = vault(st)
    sources = [store.preserve(f.name, f.getbuffer()) for f in uploads]
    for source, upload in zip(sources, uploads):
        if Path(source["name"]).suffix.lower() == '.pdf':
            import fitz
            try:
                with fitz.open(stream=bytes(upload.getbuffer()), filetype='pdf') as pdf:
                    source['pages'] = len(pdf)
            except Exception:
                raise ValueError("原本PDFを開けません。保全した原本を確認してください。") from None
        else:
            source['pages'] = 1
    if len({s["id"] for s in sources}) != len(sources):
        raise ValueError("同一原本が複数回選択されています。重複を外してください。")
    if len({s["name"] for s in sources}) != len(sources):
        raise ValueError("同名の原本があります。異なるファイル名で取り込み直してください。")
    st.session_state["_review_sources"] = sources


def capture_extraction(st, detail, summaries, records, read_errors=()):
    for key in list(st.session_state):
        if key.startswith('_review_') and key != '_review_sources':
            del st.session_state[key]
    st.session_state.pop('_vault_archive', None)
    sources = st.session_state.get("_review_sources", [])
    for source in sources:
        source['read_errors'] = [e for e in read_errors if e['source'] == source['name']]
    rows = review_rows(detail, sources)
    for r in rows:
        r["集計のみ"] = False
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
    original = st.session_state.get("_review_original_rows")
    if not original:
        return
    from conversation_ui import apply_pending, working_context, render_conversation
    apply_pending(st, vault(st))
    if st.session_state.pop('_review_clear_instruction', False):
        st.session_state['review_instruction'] = ''
    st.markdown("### 1. 読み取った見積を原本と照合")
    st.caption("原本・読取結果・訂正・承認を別々に保管します。工種は候補です。内容と施工範囲を確認してください。")
    if not os.environ.get("CYCA_DATA_DIR"):
        st.info("作業後は、原本と承認記録を左側の『バックアップ』から一括保存してください。アプリの再起動後も復元できます。")
    sources = copy.deepcopy(st.session_state["_review_sources"])
    for s in sources:
        with st.expander(f"原本：{s['name']}"):
            st.caption(f"原本ID {s['id'][:16]} / {s['size']:,} bytes")
            st.download_button("保全した原本", vault(st).read(s["id"]), file_name=Path(s["name"]).name,
                               key="review_original_" + s["id"])
            s["tax_basis"] = st.selectbox("原価の税区分", ["不明", "税抜", "税込", "非課税"], key="review_basis_"+s["id"])
            rate = st.selectbox("原本の税率", ["不明", "10%", "8%", "0%"], key="review_tax_"+s["id"])
            s["tax_rate"] = {"10%": .10, "8%": .08, "0%": 0}.get(rate)
            s["net_total"] = st.number_input("原本の税抜小計（値引き後）", min_value=0, value=0, step=1, key="review_net_"+s["id"])
            check_tax = st.checkbox("原本に税額・税込総額の記載がある", key="review_tax_exists_"+s["id"])
            if check_tax:
                s["tax"] = st.number_input("原本の税額", min_value=0, value=0, step=1, key="review_tax_amount_"+s["id"])
                s["gross_total"] = st.number_input("原本の税込総額", min_value=0, value=0, step=1, key="review_gross_"+s["id"])
            s["confirmed"] = st.checkbox("原本と明細・税区分を照合した", key="review_source_ok_"+s["id"])
    st.caption("行の追加はできません。訂正には理由、除外には施工範囲等の根拠を入力します。「集計のみ」は諸経費などを工事内容に直接載せる原本行です。原本の備考は社内で保持し、顧客へ伝える条件は「顧客備考」に入力します。")
    columns = ["採用", "品名", "仕様", "数量", "単位", "原価単価", "原価金額", "見積元", "工事種別", "部位", "顧客備考", "元ファイル", "ページ", "集計のみ", "訂正理由", "除外理由", "備考", "行ID"]
    view = pd.DataFrame(st.session_state.get('_review_assistant_rows', original))[columns]
    edited = st.data_editor(view, disabled=["行ID", "元ファイル", "備考"], hide_index=True,
                            num_rows="fixed", key="review_rows_" + str(st.session_state.get('_review_editor_epoch', 0)), width="stretch",
                            column_config={'行ID': None, '原価単価': st.column_config.NumberColumn('仕入単価', format='localized'),
                                           '原価金額': st.column_config.NumberColumn('仕入金額', format='localized')})
    by_id = {r["行ID"]: copy.deepcopy(r) for r in original}
    rows = []
    for changes in edited.to_dict("records"):
        r = by_id[changes["行ID"]]
        r.update({k: clean(v) for k, v in changes.items()})
        rows.append(r)
    conversation_slot = st.container()
    st.markdown('### 3. 決めた上乗せ・値引きと帳票内容を確認')
    st.caption('出力先：アプリ既存の横型流し込みデザイン。表紙・工事品目集計・明細を分け、明細は文字を縮めず20行ごとに改ページします。')
    with st.expander("手入力で上乗せ・値引きを調整／宛名・工事名を確認", expanded=False):
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
        m = st.session_state.get("_review_metadata", {})
        metadata = {
            "宛名": st.text_input("確認後の宛名（敬称まで）", value=str(m.get("宛名") or ""), key="review_customer"),
            "工事名称": st.text_input("確認後の工事名称", value=str(m.get("工事名称") or ""), key="review_project"),
            "見積日": st.text_input("見積日", value=datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d"), key="review_date"),
            "有効期限": "30日", "値引き理由": reason,
            "顧客税率": st.selectbox("顧客向け税率", [.10, .08, 0.0], format_func=lambda x:f"{x:.0%}", key="review_customer_tax"),
        }
    if st.session_state.get('_review_conversation_record'):
        metadata['相談反映記録'] = st.session_state['_review_conversation_record']
    with conversation_slot:
        render_conversation(st, api_key, working_context(rows, sources, targets, discount, metadata))
    resolutions = {}
    try:
        draft = build_draft(original, rows, sources, targets, discount, metadata)
        for item in draft["payload"]["指摘"]:
            if item["レベル"] == "停止":
                st.error(item["種類"] + "：" + item["内容"])
            else:
                st.warning(item["種類"] + "：" + item["内容"] + " 対象行：" + ", ".join(item["行"]))
                resolutions[item["id"]] = st.text_input("確認結果・根拠", key="review_resolution_"+item["id"])
        draft = build_draft(original, rows, sources, targets, discount, metadata, resolutions)
    except (ValueError, TypeError, KeyError) as exc:
        st.error(str(exc))
        return
    totals = draft["payload"]["合計"]
    st.text('宛名：' + metadata['宛名'] + '　／　工事名称：' + metadata['工事名称'])
    cost_card, markup_card, total_card = st.columns(3)
    cost_card.metric('仕入原価（税抜）', f"{totals['仕入原価']:,}円")
    markup_card.metric('上乗せ額（値引き前）', f"{totals['売価小計'] - totals['仕入原価']:,}円")
    total_card.metric('お客様への税込見積額', f"{totals['税込売価']:,}円")
    if discount:
        st.caption(f"顧客値引き：{discount:,}円 ／ 値引き後の粗利益：{totals['粗利益']:,}円")
    with st.expander('原価・売価・税・粗利益の内訳を確認'):
        st.dataframe(pd.DataFrame([totals]), hide_index=True, width="stretch")
    result_rows = pd.DataFrame(draft["payload"]["計算明細"])
    st.dataframe(result_rows[[c for c in ("品名", "数量", "単位", "原価単価", "原価金額", "見積単価", "見積金額", "上乗せ額", "見積元", "工事種別") if c in result_rows]], hide_index=True, width="stretch")
    if not result_rows.empty and "見積金額" in result_rows:
        for col in ("見積元", "工事種別"):
            st.write(col + "ごとの原価・売価")
            st.dataframe(result_rows.groupby(col, sort=False)[["原価金額", "見積金額", "上乗せ額"]].sum(), width="stretch")
    valid = not draft["unresolved"] and bool(metadata["宛名"].strip()) and bool(metadata["工事名称"].strip())
    agree = st.checkbox("原本、工種、原価・売価、税、重複疑い、値引きを確認し、この内容を承認する", key="review_agree_"+draft["fingerprint"])
    if st.button("確定して彩架建設の見積書とPDFを作成", disabled=not (valid and agree), key="review_approve"):
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
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    approval = st.session_state.get("_review_approval")
    result = st.session_state.get("_review_result")
    if approval and result and approval["fingerprint"] == draft["fingerprint"] and valid and agree:
        st.success("承認内容と現在の入力が一致しています。数量や目標などを変えると再承認が必要です。")
        st.download_button("承認済み見積書（Numbers用xlsx）", result.data, file_name=result.file_name, key="review_xlsx")
        st.download_button("承認済みPDF", result.pdf_data, file_name=result.file_name.removesuffix('.xlsx')+'.pdf', key="review_pdf")
        st.download_button("承認記録（原価を含む社内用）", canonical(approval).encode(), file_name="社内承認記録.json", key="review_receipt")
        try:
            render_backup_download(st, 'review')
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    elif result:
        st.info("入力が変更されたため、前回の承認済みファイルを非表示にしました。再承認してください。")

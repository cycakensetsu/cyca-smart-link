"""Read one upload; retry an empty PDF response as page images once."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from gemini_resilience import (call_gemini_with_retry, build_page_prompt,
                               parse_gemini_json_payload, _render_pdf_pages_as_images)
from estimate_pipeline import (split_extraction_payload, build_intermediate_dataframe,
                               validate_intermediate, original_source_notes)
from source_table import reconcile_native_pdf
from source_sections import reconcile_sections, reconcile_printed_totals


def _total_mismatch(records, summaries, source_name):
    from source_amounts import money
    scoped = [s for s in summaries if s.get('__source_name') == source_name]
    from document_scopes import printed_scopes
    sections=printed_scopes(scoped,source_name)
    if sections:
        values=[money(r.get('金額')) for r in records if r.get('__source_name')==source_name]
        return any(v is None for v in values) or sum(values)!=sum(s['net_total'] for s in sections)
    finals = [money(s.get('改小計',s.get('税抜合計'))) for s in scoped if s.get('__page_role') in ('cover_summary_page','summary_page')]
    if not finals: finals = [money(s.get('改小計',s.get('税抜合計'))) for s in scoped]
    finals = [n for n in finals if n is not None]
    if not finals:
        finals = [money(s['工事費計']) - money(s['消費税']) for s in scoped
                  if money(s.get('工事費計')) is not None and money(s.get('消費税')) is not None]
    if not finals:
        finals = [money(s.get('小計')) for s in scoped if s.get('__page_role') in ('cover_summary_page','summary_page')]
        finals = [n for n in finals if n is not None]
    if not finals: return False
    values = [money(r.get('金額')) for r in records if r.get('__source_name') == source_name]
    return len(set(finals)) != 1 or any(n is None for n in values) or sum(n for n in values if n is not None) != finals[0]


def _reread_summaries(previous, reread):
    # The page-by-page reading supersedes the broad PDF reading, while keeping
    # descriptive metadata it could not see. Never mix their detail rows.
    if not reread: return previous
    result=[]
    for summary in reread:
        same_source=[s for s in previous if s.get('__source_name') == summary.get('__source_name')]
        base=next((s for s in same_source if s.get('__page_number') is not None
                   and s.get('__page_number') == summary.get('__page_number')), None)
        if base is None:
            old=next((s for s in same_source if s.get('__page_role') == summary.get('__page_role') == 'cover_summary_page'),{})
            base={k:v for k,v in old.items() if k in ('宛名','工事名称','工事名','工事場所','見積元','支払条件','有効期限')}
        result.append({**base, **{k:v for k,v in summary.items() if v not in (None,'',[])}})
    # A reread may omit a footer or replace a page's notes. Keep the earlier
    # printed words at their original location, without carrying over money.
    known = [note for summary in result for note in original_source_notes(summary)]
    for old in previous:
        for note in original_source_notes(old):
            if note in known:
                continue
            target = next((summary for summary in result
                           if summary.get('__source_name') == note['元ファイル']
                           and summary.get('__page_number') == note['ページ']), None)
            if target is None:
                target = {'__source_name': note['元ファイル'], '__page_number': note['ページ'],
                          '__page_role': 'detail_page'}
                result.append(target)
            target['原本備考'] = original_source_notes(target) + [note]
            known.append(note)
    return result


def include_printed_discount(records, summaries, source_name, total_pages):
    """Recover a printed summary discount, never the unexplained residual.

    Require source/page, an explicit negative adjustment and its printed text,
    both printed subtotals, and reconciliation to every existing detail row.
    A duplicate, conflict, unknown number, or missing expense stays blocked.
    """
    import re
    import unicodedata
    from decimal import Decimal, InvalidOperation
    def integer(value):
        try:
            n = Decimal(str(value).replace(',', ''))
            return int(n) if n.is_finite() and n == n.to_integral_value() else None
        except (ValueError, InvalidOperation): return None
    scoped = [r for r in records if r.get('__source_name') == source_name]
    values = [integer(r.get('金額')) for r in scoped]
    if not scoped or any(v is None for v in values): return records
    candidates = []
    for summary in summaries:
        if summary.get('__source_name') != source_name: continue
        adjustment = next((integer(summary[k]) for k in ('端数調整','値引き','値引','出精値引き') if summary.get(k) is not None), None)
        subtotal = integer(summary.get('小計'))
        final = integer(summary.get('改小計', summary.get('税抜合計')))
        evidence = unicodedata.normalize('NFKC',str(summary.get('抽出元テキスト範囲') or '')).replace(',', '')
        page = integer(summary.get('__page_number'))
        if (adjustment is None or adjustment >= 0 or subtotal is None or final is None
                or subtotal + adjustment != final or sum(values) != subtotal
                or page is None or not 1 <= page <= total_pages): continue
        if not re.search('値引|端数調整|出精', evidence) or str(abs(adjustment)) not in evidence: continue
        if any(integer(r.get('金額')) == adjustment and re.search('値引|調整|出精',str(r.get('品名',''))) for r in scoped): continue
        candidates.append((adjustment, page, evidence, summary))
    if len({(v,p) for v,p,_,_ in candidates}) != 1: return records
    value,page,evidence,summary = candidates[0]
    vendors = list(dict.fromkeys(str(r.get('見積元') or '').strip() for r in scoped if r.get('見積元')))
    vendor = summary.get('見積元') or (vendors[0] if len(vendors) == 1 else None)
    return records + [{'品名':'値引き','仕様':'原本に記載された値引き','数量':1,'単位':'式',
                       '単価':value,'金額':value,'見積元':vendor,'工事種別':'諸経費',
                       '__source_name':source_name,'__page_number':page,'__summary_only':True,
                       '抽出元テキスト範囲':evidence}]


def extract_job(client, page, path, n_files, *, primary_model, fallback_models,
                on_retry=None, on_model_start=None, client_factory=None,
                network_gate=None, max_network_workers=4):
    models=[]
    if network_gate is None:
        from extraction_batch import _NETWORK_GATE
        network_gate=_NETWORK_GATE

    def read(job, reader_client, *, gate_held=False):
        # Only network work takes this shared gate. Native PDF parsing retains
        # its separate one-process limit and cannot deadlock network readers.
        with (nullcontext() if gate_held else network_gate):
            result=call_gemini_with_retry(reader_client,job.parts+[build_page_prompt(job,n_files)],
                primary_model=primary_model,fallback_models=fallback_models,
                page_number=None if job.whole_document else job.page_number,
                source_name=job.source_name,on_retry=on_retry,on_model_start=on_model_start)
        if not result.text:
            return [],[],False,result.model_name
        try:
            summaries,records=split_extraction_payload(parse_gemini_json_payload(result.text),
                source_name=job.source_name,page_number=None if job.whole_document else job.page_number)
            summaries=[reconcile_printed_totals(s) for s in summaries]
            return summaries,records,True,result.model_name
        except json.JSONDecodeError:
            return [],[],False,result.model_name

    summaries,records,ok,model=read(page,client)
    models.append(model)
    errors=[]
    native_reconciled=False
    if page.whole_document and records:
        records,summaries=reconcile_native_pdf(path,records,summaries)
        native_reconciled=True
    records=reconcile_sections(records,summaries,page.source_name)
    records=include_printed_discount(records,summaries,page.source_name,page.total_pages)
    frame,_=build_intermediate_dataframe(records)
    incomplete = bool(validate_intermediate(frame)) or frame.empty
    def valid_page(row):
        value=row.get('__page_number')
        if value in (None,''):
            return page.total_pages == 1
        try:
            number=float(value)
            return number.is_integer() and 1 <= number <= page.total_pages
        except (TypeError,ValueError):
            return False
    missing_pages = any(not valid_page(row) for row in records)
    covered={int(float(r['__page_number'])) for r in records+summaries
             if r.get('__page_number') not in (None,'') and valid_page(r)}
    incomplete_coverage=page.total_pages>1 and covered != set(range(1,page.total_pages+1))
    mismatched = _total_mismatch(records,summaries,page.source_name)
    if page.whole_document and (not records or incomplete or missing_pages or mismatched or incomplete_coverage):
        native_reconciled=False
        whole_summaries=summaries
        # No partial PDF rows are combined with another reading. This avoids
        # doubling details when the image retry sees the same printed item.
        summaries,records=[],[]
        image_pages=_render_pdf_pages_as_images(path,page.source_name)
        def read_image(image_page):
            reader_client=None
            try:
                # Do not construct many SDK/TLS clients while waiting for a
                # network slot. Only active page readers own an extra client.
                with network_gate:
                    try:
                        reader_client=client_factory()
                        ss,rr,success,used_model=read(image_page,reader_client,gate_held=True)
                        failed=[] if success else [{'source':page.source_name,'page':image_page.page_number,'reason':'read_failed'}]
                        return ss,rr,used_model,failed
                    finally:
                        if reader_client is not None and callable(getattr(reader_client,'close',None)):
                            try:reader_client.close()
                            except Exception:pass
            except Exception as error:
                from extraction_batch import extraction_failure_reason
                return [],[],None,[{'source':page.source_name,'page':image_page.page_number,
                                   'reason':extraction_failure_reason(error)}]
        if client_factory is not None and len(image_pages)>1:
            with ThreadPoolExecutor(max_workers=max(1,min(max_network_workers,4,len(image_pages)))) as executor:
                futures=[executor.submit(read_image,image_page) for image_page in image_pages]
                # Gather in physical-page order, irrespective of completion order.
                for future in futures:
                    ss,rr,used_model,failed=future.result()
                    summaries.extend(ss);records.extend(rr);errors.extend(failed)
                    if used_model:models.append(used_model)
        else:
            for image_page in image_pages:
                page_summaries,page_records,page_ok,used_model=read(image_page,client)
                summaries.extend(page_summaries);records.extend(page_records);models.append(used_model)
                if not page_ok:
                    errors.append({'source':page.source_name,'page':image_page.page_number})
        summaries=_reread_summaries(whole_summaries,summaries)
    elif not ok:
        errors.append({'source':page.source_name,'page':page.page_number})
    if page.whole_document:
        if not records:
            errors.append({'source':page.source_name,'page':None,'reason':'no_details'})
        elif not native_reconciled:
            records,summaries=reconcile_native_pdf(path,records,summaries)
    records=reconcile_sections(records,summaries,page.source_name)
    records=include_printed_discount(records,summaries,page.source_name,page.total_pages)
    from document_scopes import retain_scope_context
    records=retain_scope_context(records,summaries,page.source_name)
    if page.whole_document and str(path).lower().endswith('.pdf'):
        from source_table import attach_native_dimensions
        try:
            records=attach_native_dimensions(path,records)
        except (ValueError,RuntimeError,AttributeError):
            pass  # Unsupported native geometry keeps the original AI reading.
    if ok:
        from source_annotations import verify_scanned_annotations
        summaries, annotation_model = verify_scanned_annotations(
            client, page, path, summaries, primary_model=primary_model,
            fallback_models=fallback_models, network_gate=network_gate,
            on_retry=on_retry, on_model_start=on_model_start)
        if annotation_model:
            models.append(annotation_model)
    return summaries,records,models,errors

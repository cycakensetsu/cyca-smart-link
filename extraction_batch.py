"""Bounded, session-local extraction: no Streamlit calls from worker threads."""
import copy
import hashlib
from pathlib import Path
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
import threading

MAX_NETWORK_WORKERS = 4
# Preserve the same gate when coordinated source reloads overlap old workers.
_NETWORK_GATE = globals().get('_NETWORK_GATE') or threading.BoundedSemaphore(MAX_NETWORK_WORKERS)


def extraction_failure_reason(error):
    """Return an allowlisted reason, never provider text or credentials."""
    text=str(error).lower()
    reason=str(getattr(error,'reason','')).lower()
    value=text+' '+reason
    codes=[getattr(error,key,None) for key in ('code','status_code','error_code')]
    if (401 in codes or any(token in value for token in (
            'api_key_invalid','invalid_api_key','api_key_expired','api key not valid',
            'api key is invalid','invalid api key','api key expired','api key has expired',
            'unauthenticated','api key was reported as leaked'))):
        return 'credential_invalid'
    if 429 in codes or any(token in value for token in ('resource_exhausted','quota exceeded','quota_exceeded','rate_limit_exceeded')):
        return 'quota'
    if (any(code in codes for code in (408,500,502,503,504))
            or type(error).__name__ in ('GeminiTemporaryUnavailable','TimeoutError','ReadTimeout','ConnectTimeout','ConnectError')):
        return 'temporary'
    return 'read_failed'


def extraction_notice(errors, *, empty=True):
    """One actionable notice for a batch; do not blame files for API failures."""
    reasons={item.get('reason') for item in errors if isinstance(item,dict)}
    if 'credential_invalid' in reasons:
        return 'AIの接続設定を確認する必要があります。管理者が設定を更新したあと、もう一度「見積書を読み取る」を押してください。原本は保持しています。'
    if 'quota' in reasons:
        return 'AIの利用上限に達しています。利用枠を確認してから、もう一度「見積書を読み取る」を押してください。原本は保持しています。'
    if 'temporary' in reasons:
        return 'AIが混み合っているか、接続に時間がかかっています。少し待って、もう一度「見積書を読み取る」を押してください。原本は保持しています。'
    if not empty:
        return '読み直しが必要な見積があります。下で対象の見積を確認できます。'
    if 'read_failed' in reasons:
        return '読み取りを完了できませんでした。原本は保持しています。もう一度「見積書を読み取る」を押してください。'
    return '見積の品目を読み取れませんでした。原本は保持しています。品目・数量・単価が載った見積書か確認し、もう一度読み取ってください。'


def pipeline_version():
    digest=hashlib.sha256()
    for name in ('extraction_batch','source_extraction','source_annotations','source_table','source_sections',
                 'document_scopes','estimate_pipeline','gemini_resilience','cyca_reference','source_amounts','markup_rules','pdf_worker','complete_document_cache'):
        digest.update((Path(__file__).parent/(name+'.py')).read_bytes())
    return digest.hexdigest()


def extract_batch(jobs, paths, n_files, *, client_factory, primary_model,
                  fallback_models, cache=None, max_workers=4, extractor=None):
    """Yield UI-safe progress events; complete payloads in original input order.

    Results stay in the caller's authenticated session, keyed by original bytes,
    exact filename, model settings and parser revision. Failed reads are retried.
    Each network worker owns its client. Native PDF work stays process-isolated.
    """
    from source_extraction import extract_job
    from source_annotations import annotation_cache_ready
    production_extractor = extractor is None or extractor is extract_job
    if extractor is None:extractor=extract_job
    cache=cache if cache is not None else {}
    version=pipeline_version()
    events=Queue();results={};pending=[];keys={}
    for index,job in enumerate(jobs):
        data=Path(paths[id(job)]).read_bytes()
        key=hashlib.sha256(repr((hashlib.sha256(data).hexdigest(),job.source_name,
            job.page_number,job.total_pages,job.whole_document,primary_model,
            tuple(fallback_models),version)).encode()).hexdigest()
        keys[index]=key
        if key in cache and not annotation_cache_ready(cache[key][0]):
            cache.pop(key)
        if key in cache:
            results[index]=copy.deepcopy(cache[key])
            yield {'kind':'done','index':index,'source':job.source_name,'cached':True,'seconds':0}
        else:pending.append((index,job))

    def work(index,job):
        started=monotonic();client=None
        try:
            client=client_factory()
            result=extractor(client,job,paths[id(job)],n_files,
                primary_model=primary_model,fallback_models=fallback_models,
                **({'client_factory':client_factory,'network_gate':_NETWORK_GATE,
                    'max_network_workers':max(1,min(max_workers,MAX_NETWORK_WORKERS))} if production_extractor else {}),
                on_retry=lambda model,attempt,delay,code,page:events.put(
                    {'kind':'retry','index':index,'source':job.source_name,'seconds':delay,'page':page}),
                on_model_start=lambda model,page:events.put(
                    {'kind':'reading','index':index,'source':job.source_name,'page':page}))
        except Exception as error:
            # Provider exception messages may contain document data or credentials.
            result=([],[],[],[{'source':job.source_name,'page':None,'reason':extraction_failure_reason(error)}])
        finally:
            if client is not None and callable(getattr(client,'close',None)):
                try:client.close()
                except Exception:pass
        events.put({'kind':'done','index':index,'source':job.source_name,
                    'cached':False,'seconds':monotonic()-started,'result':result})

    workers=max(1,min(max_workers,MAX_NETWORK_WORKERS))
    batch_started=monotonic();last_wait=batch_started
    yield {'kind':'started','total':len(jobs),'cached':len(results),'workers':min(workers,len(pending))}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures=[executor.submit(work,index,job) for index,job in pending]
        completed=0
        while completed<len(pending):
            try:event=events.get(timeout=.2)
            except Empty:
                now=monotonic()
                if now-last_wait>=1:
                    yield {'kind':'waiting','seconds':now-batch_started,
                           'active':sum(f.running() for f in futures),'completed':len(results)}
                    last_wait=now
                continue
            if event['kind']=='done':
                index=event['index'];result=event.pop('result');results[index]=result;completed+=1
                from estimate_pipeline import build_intermediate_dataframe,validate_intermediate
                from source_extraction import _total_mismatch
                frame,_=build_intermediate_dataframe(result[1])
                cacheable=(bool(result[1]) and not result[3] and not validate_intermediate(frame)
                           and annotation_cache_ready(result[0]))
                def linked(row):
                    try:
                        page=float(row.get('__page_number',1 if jobs[index].total_pages==1 else 0))
                        return (row.get('__source_name')==event['source'] and page.is_integer()
                                and 1<=page<=jobs[index].total_pages)
                    except (TypeError,ValueError):return False
                cacheable=cacheable and all(linked(row) for row in result[1])
                cacheable=cacheable and not _total_mismatch(result[1],result[0],event['source'])
                if cacheable:
                    if len(cache)>=20:cache.pop(next(iter(cache)))
                    cache[keys[index]]=copy.deepcopy(result)
            yield event
            now=monotonic()
            if now-last_wait>=1 and completed<len(pending):
                yield {'kind':'waiting','seconds':now-batch_started,
                       'active':sum(f.running() for f in futures),'completed':len(results)}
                last_wait=now
        # Surface unexpected executor errors instead of silently waiting forever.
        for future in futures:future.result()
    yield {'kind':'result','results':[results[index] for index in range(len(jobs))]}


def reconcile_document_results(sources,records,summaries):
    """Join page reads only after every physical page has a retained result."""
    from source_sections import reconcile_sections,reconcile_printed_totals
    from source_extraction import include_printed_discount
    from document_scopes import retain_scope_context,verify_scope_boundaries
    summaries=[reconcile_printed_totals(s) for s in summaries]
    for source in sources:
        name=source['name']
        records=reconcile_sections(records,summaries,name)
        records=include_printed_discount(records,summaries,name,source['pages'])
        summaries=verify_scope_boundaries(records,summaries,name)
        records=retain_scope_context(records,summaries,name)
    return records,summaries

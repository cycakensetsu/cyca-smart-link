"""Session-local, validated whole-document cache checked before PDF preparation.

The caller owns the dictionary. No credentials, file paths, shared cloud state,
or approval decisions belong in this cache. A cache hit avoids both image
preparation and network work; its raw results still enter the normal review.
"""
import copy
import hashlib
import json
import re

MAX_DOCUMENTS = 20
MAX_CACHE_BYTES = 32 * 1024 * 1024


def _bytes(result):
    try:return len(json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())
    except (ValueError,TypeError,OverflowError):return None


def _key(source, primary_model, fallback_models):
    from extraction_batch import pipeline_version
    raw_sha=str(source.get('id',''))
    name=source.get('name')
    pages=source.get('pages')
    if not re.fullmatch('[a-f0-9]{64}',raw_sha) or not isinstance(name,str):return None
    if type(pages) is not int or pages<1:return None
    return hashlib.sha256(repr((raw_sha,name,pages,primary_model,
        tuple(fallback_models),pipeline_version())).encode()).hexdigest()


def complete(source, result):
    """No partial read, stale source link, missing page, or mismatch is a hit."""
    from estimate_pipeline import build_intermediate_dataframe,validate_intermediate
    from source_extraction import _total_mismatch
    from source_amounts import money
    from source_annotations import annotation_cache_ready
    try:
        summaries,records,models,errors=result
        if errors or not records or not isinstance(summaries,list) or not isinstance(records,list):return False
        if not annotation_cache_ready(summaries):return False
        pages=source['pages'];name=source['name']
        if type(pages) is not int or pages<1:return False
        covered=set()
        for is_detail,items in ((True,records),(False,summaries)):
            for row in items:
                if not isinstance(row,dict) or row.get('__source_name')!=name:return False
                value=row.get('__page_number')
                if value in (None,''):
                    if pages==1:covered.add(1)
                    # Whole-file summaries can lack a page. They never prove one.
                    elif is_detail:return False
                    continue
                page=float(value)
                if not page.is_integer() or not 1<=page<=pages:return False
                covered.add(int(page))
        if covered!=set(range(1,pages+1)):return False
        if not any(any(money(s.get(k)) is not None for k in
                ('改小計','税抜合計','小計','工事費計')) for s in summaries):return False
        frame,_=build_intermediate_dataframe(records)
        return not validate_intermediate(frame) and not _total_mismatch(records,summaries,name)
    except (ValueError,TypeError,KeyError,OverflowError):
        return False


def get_document(cache, source, *, primary_model, fallback_models):
    """Return (summaries, records, models, errors), or None before preparation."""
    key=_key(source,primary_model,fallback_models)
    if key is None or key not in cache:return None
    result=cache[key]
    size=_bytes(result)
    if size is None or size>MAX_CACHE_BYTES or not complete(source,result):
        cache.pop(key,None)
        return None
    return copy.deepcopy(result)


def put_document(cache, source, result, *, primary_model, fallback_models):
    """Store raw extraction only after document-level reconciliation succeeds."""
    key=_key(source,primary_model,fallback_models)
    size=_bytes(result)
    if key is None or size is None or size>MAX_CACHE_BYTES or not complete(source,result):return False
    cache.pop(key,None)
    sizes={}
    for old,entry in list(cache.items()):
        entry_size=_bytes(entry)
        if entry_size is None or entry_size>MAX_CACHE_BYTES:cache.pop(old,None)
        else:sizes[old]=entry_size
    total=sum(sizes.values())
    while cache and (len(cache)>=MAX_DOCUMENTS or total+size>MAX_CACHE_BYTES):
        old=next(iter(cache));cache.pop(old);total-=sizes[old]
    cache[key]=copy.deepcopy(result)
    return True

"""Read one upload; retry an empty PDF response as page images once."""
import json
from gemini_resilience import (call_gemini_with_retry, build_page_prompt,
                               parse_gemini_json_payload, _render_pdf_pages_as_images)
from estimate_pipeline import split_extraction_payload, build_intermediate_dataframe, validate_intermediate
from source_table import reconcile_native_pdf


def extract_job(client, page, path, n_files, *, primary_model, fallback_models,
                on_retry=None, on_model_start=None):
    models=[]

    def read(job):
        result=call_gemini_with_retry(client,job.parts+[build_page_prompt(job,n_files)],
            primary_model=primary_model,fallback_models=fallback_models,
            page_number=None if job.whole_document else job.page_number,
            source_name=job.source_name,on_retry=on_retry,on_model_start=on_model_start)
        models.append(result.model_name)
        if not result.text:
            return [],[],False
        try:
            summaries,records=split_extraction_payload(parse_gemini_json_payload(result.text),
                source_name=job.source_name,page_number=None if job.whole_document else job.page_number)
            return summaries,records,True
        except json.JSONDecodeError:
            return [],[],False

    summaries,records,ok=read(page)
    errors=[]
    if page.whole_document and records:
        records,summaries=reconcile_native_pdf(path,records,summaries)
    frame,_=build_intermediate_dataframe(records)
    incomplete = bool(validate_intermediate(frame)) or frame.empty
    missing_pages = page.total_pages > 1 and any(not r.get('__page_number') for r in records)
    if page.whole_document and (not records or incomplete or missing_pages):
        whole_summaries=summaries
        # No partial PDF rows are combined with another reading. This avoids
        # doubling details when the image retry sees the same printed item.
        summaries,records=[],[]
        for image_page in _render_pdf_pages_as_images(path,page.source_name):
            page_summaries,page_records,page_ok=read(image_page)
            summaries.extend(page_summaries);records.extend(page_records)
            if not page_ok:
                errors.append({'source':page.source_name,'page':image_page.page_number})
        if whole_summaries:
            summaries=whole_summaries
    elif not ok:
        errors.append({'source':page.source_name,'page':page.page_number})
    if page.whole_document:
        if not records:
            errors.append({'source':page.source_name,'page':None,'reason':'no_details'})
        else:
            records,summaries=reconcile_native_pdf(path,records,summaries)
    return summaries,records,models,errors

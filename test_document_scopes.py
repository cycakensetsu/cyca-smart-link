import unittest
from document_scopes import printed_scopes,retain_scope_context
from estimate_pipeline import split_extraction_payload,normalize_summary_data
from source_amounts import read_amounts


class DocumentScopesTest(unittest.TestCase):
    def covers(self):
        return [dict(__source_name='原本　2件.pdf',__page_role='cover_summary_page',__page_number=p,
                     工事名称=n,改小計=v,消費税=v//10,工事費計=v,抽出元テキスト範囲='印字')
                for p,n,v in [(1,'本体工事',1000),(4,'車庫工事',2000)]]

    def test_distinct_cover_totals_combine_without_losing_sections_or_source(self):
        covers=self.covers();sections=printed_scopes(covers,'原本　2件.pdf')
        self.assertEqual([s['page'] for s in sections],[1,4])
        reading=read_amounts('原本　2件.pdf',b'not-pdf',normalize_summary_data(covers))
        self.assertEqual((reading['net_total'],reading['tax'],reading['gross_total']),(3000,300,3300))
        records=[dict(__source_name='原本　2件.pdf',__page_number=p,部位='外壁') for p in (2,5)]
        rows=retain_scope_context(records,covers,'原本　2件.pdf')
        self.assertEqual([r['部位'] for r in rows],['本体工事 ｜ 外壁','車庫工事 ｜ 外壁'])
        self.assertEqual(retain_scope_context(rows,covers,'原本　2件.pdf'),rows)
        self.assertEqual(records[0]['部位'],'外壁')

    def test_missing_or_conflicting_cover_totals_do_not_get_guessed(self):
        covers=self.covers();covers[1].pop('改小計')
        self.assertEqual(printed_scopes(covers,'原本　2件.pdf'),[])
        covers=self.covers();covers.append(dict(covers[0],改小計=1500))
        self.assertEqual(printed_scopes(covers,'原本　2件.pdf'),[])

    def test_whole_pdf_may_return_distinct_summary_objects(self):
        summaries,_=split_extraction_payload({'summary_data':self.covers(),'detail_data':[]},'原本　2件.pdf')
        self.assertEqual(len(summaries),2)
        self.assertEqual([s['__page_number'] for s in summaries],[1,4])
        self.assertEqual(printed_scopes(summaries,'別原本.pdf'),[])


class GenericSummaryScopeTest(unittest.TestCase):
    def test_generic_summary_requires_separate_detail_evidence_before_combining(self):
        from document_scopes import verify_scope_boundaries
        source='a.pdf'
        summaries=[dict(__source_name=source,__page_number=p,__page_role=role,工事名称=n,改小計=v,消費税=v//10,工事費計=v)
            for p,role,n,v in [(1,'cover_summary_page','本体',1000),(4,'summary_page','車庫',2000)]]
        rows=[dict(__source_name=source,__page_number=p,金額=v) for p,v in [(2,1000),(5,2000)]]
        self.assertEqual(printed_scopes(summaries,source),[])
        verified=verify_scope_boundaries(rows,summaries,source)
        self.assertEqual(len(printed_scopes(verified,source)),2)
        wrong=[dict(rows[0],金額=900),rows[1]]
        self.assertEqual(printed_scopes(verify_scope_boundaries(wrong,summaries,source),source),[])

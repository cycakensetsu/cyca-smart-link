"""Original filenames must remain usable through the complete review boundary."""
import unittest
from estimate_pipeline import split_extraction_payload,build_intermediate_dataframe,normalize_summary_data
from estimate_review import review_rows
from source_review_ui import source_title
from product_design import ready_panel


class ImportIdentityTest(unittest.TestCase):
    def test_realistic_filenames_round_trip_without_aliases(self):
        names=['外部塗装工事　御見積書 2.pdf','プラムカ\u3099ーテ\u3099ン様　御見積書.pdf',
               '工事  内訳.pdf','工事　内訳.pdf','工事 内訳.pdf',' 工事.pdf',
               '会社【A】\n内訳.pdf','設備<&>.pdf']
        sources=[{'id':str(i),'name':name,'pages':1} for i,name in enumerate(names)]
        all_rows=[];all_summaries=[]
        for i,name in enumerate(names):
            payload={'page_role':'cover_summary_page','summary_data':{'工事名称':f'確認工事{i}','小計':100},
                     'detail_data':[{'品名':'商品と取付','数量':1,'単位':'式','単価':100,'金額':100}]}
            summaries,rows=split_extraction_payload(payload,name,1)
            all_rows.extend(rows);all_summaries.extend(summaries)
        frame,_=build_intermediate_dataframe(all_rows)
        reviewed=review_rows(frame,sources);metadata=normalize_summary_data(all_summaries)
        self.assertEqual([r['元ファイル'] for r in reviewed],names)
        self.assertEqual([r['原本ID'] for r in reviewed],[str(i) for i in range(len(names))])
        self.assertEqual([source_title(s,metadata,len(sources)) for s in sources],[f'確認工事{i}' for i in range(len(names))])

    def test_model_cannot_reassign_a_row_to_another_original(self):
        payload={'summary_data':{'__source_name':'other.pdf','__page_number':99},
                 'detail_data':[{'__source_name':'other.pdf','__page_number':99,'品名':'部材'}]}
        summaries,rows=split_extraction_payload(payload,'actual　原本.pdf',2)
        self.assertEqual(rows[0]['__source_name'],'actual　原本.pdf')
        self.assertEqual(summaries[0]['__source_name'],'actual　原本.pdf')
        self.assertEqual(rows[0]['__page_number'],2)
        self.assertEqual(summaries[0]['__page_number'],2)
        _,legacy=split_extraction_payload(payload['detail_data'],'actual　原本.pdf',2)
        self.assertEqual(legacy[0]['__source_name'],'actual　原本.pdf')

    def test_received_list_is_one_full_escaped_name_per_row(self):
        html=ready_panel(['工事　内訳.pdf','設備<&>.pdf'])
        self.assertEqual(html.count('<li>'),2)
        self.assertIn('工事　内訳.pdf',html)
        self.assertIn('設備&lt;&amp;&gt;.pdf',html)
        self.assertNotIn(' ／ ',html)

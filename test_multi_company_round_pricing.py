"""3/5 real synthetic PDFs -> native import -> review -> proposal -> outputs."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from estimate_pipeline import build_intermediate_dataframe
from estimate_review import OriginalVault, Target, review_rows, build_draft, approve, build_approved_estimate, preview_allowed
from pricing_options import choice_for
from pricing_options_ui import reconcile_inputs
from source_table import read_native_tables


def create_sources(folder, count):
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    paths=[]
    for index in range(count):
        name=f'検証{index+1}社'
        items=[(f'{name} 主工事',20+index,'m',10000),
               (f'{name} タッチ補修',10+index,'㎡',50),
               (f'{name} 加工',10+index,'個',400),
               (f'{name} 法定福利費',1,'式',20621),
               (f'{name} 原本値引き',1,'式',-1000)]
        data=[['品名','数量','単位','単価','金額']]
        data += [[n,str(q),u,f'{p:,}',f'{q*p:,}'] for n,q,u,p in items]
        total=sum(q*p for _,q,_,p in items)
        data.append(['小計','','','',f'{total:,}'])
        path=folder/f'synthetic-vendor-{index+1}.pdf'
        table=Table(data,colWidths=[230,55,40,80,90])
        table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'HeiseiKakuGo-W5'),('FONTSIZE',(0,0),(-1,-1),10),('GRID',(0,0),(-1,-1),.5,'black'),('ROWBACKGROUNDS',(0,0),(-1,0),['#dddddd'])]))
        style=getSampleStyleSheet()['Normal'];style.fontName='HeiseiKakuGo-W5'
        SimpleDocTemplate(str(path)).build([Paragraph(f'{name} 架空見積書・検証専用・提出不可',style),Spacer(1,20),table])
        paths.append(path)
    return paths


def import_sources(paths, vault):
    records=[];sources=[]
    for index,path in enumerate(paths):
        rows,subtotals,_=read_native_tables(str(path))
        if len(rows)!=5:raise ValueError(f'架空PDFの読取行数が不正です: {path} {len(rows)}')
        cost=sum(int(r['金額']) for r in rows)
        if cost not in subtotals:raise ValueError('架空PDFの小計不一致')
        source=vault.preserve(path.name,path.read_bytes())
        source.update(pages=1,confirmed=True,tax_basis='税抜',tax_rate=.1,net_total=cost)
        sources.append(source)
        for row in rows:row.update(見積元=f'検証{index+1}社',工事種別=f'工種{index+1}',__source_name=path.name)
        records.extend(rows)
    detail,_=build_intermediate_dataframe(records)
    rows=review_rows(detail,sources)
    return rows,sources


def settings_for(rows):
    return {'step':50,'limit':100000,'welfare_reason':'架空の算定根拠を確認した検証用の比較案',
            'rates':{r['行ID']:(150 if 'タッチ' in r['品名'] else 500 if '加工' in r['品名'] else 30000)
                     for r in rows if any(x in r['品名'] for x in ('タッチ','加工','法定福利'))}}


class MultiCompanyRoundPricingTest(unittest.TestCase):
    def run_case(self,count):
        with tempfile.TemporaryDirectory() as temp:
            output=os.environ.get('CYCA_MULTI_QUOTE_OUTPUT_DIR')
            folder=Path(output)/f'{count}-companies' if output else Path(temp)/'fixtures'
            paths=create_sources(folder,count)
            vault=OriginalVault(str(Path(temp)/'vault'),'multi-company-test')
            rows,sources=import_sources(paths,vault)
            self.assertEqual(len(rows),count*5)
            self.assertEqual(len({s['id'] for s in sources}),count)
            before=copy.deepcopy(rows)
            targets=[Target('全体','全体','上乗せ額',350000)]
            meta={'宛名':'架空見積検証 御中','工事名称':f'{count}社統合試験・提出不可','顧客税率':.1,'見積日':'2026/11/09'}
            ordinary=build_draft(rows,copy.deepcopy(rows),sources,targets,0,meta)
            self.assertTrue(ordinary['profit_plan']['requires_confirmation'])
            self.assertFalse(preview_allowed(ordinary))
            quick={'step':50,'limit':100000,'welfare_reason':'','rates':{r['行ID']:150 for r in rows if 'タッチ' in r['品名']}}
            from pricing_options import build_options
            quick_option=build_options(rows,targets,rounding=quick)
            self.assertIsNotNone(quick_option['proposal'],quick_option['proposal_error'])
            self.assertEqual(quick_option['proposal']['totals']['最終利益'],350000)
            for r in quick_option['proposal']['rows']:
                if 'タッチ' in r['品名']:self.assertEqual(r['見積単価'],150)
                if '法定福利' in r['品名']:self.assertEqual(r['見積金額'],r['原価金額'])
            config=settings_for(rows);meta['個別単価案']=config
            pending=build_draft(rows,copy.deepcopy(rows),sources,targets,0,meta)
            self.assertFalse(preview_allowed(pending))
            self.assertIsNotNone(pending['pricing_options']['proposal'],pending['pricing_options']['proposal_error'])
            choice=choice_for(pending['pricing_options'],True)
            chosen=build_draft(rows,copy.deepcopy(rows),sources,targets,0,meta,pricing_choice=choice)
            self.assertEqual(chosen['unresolved'],[])
            self.assertEqual(chosen['payload']['合計']['粗利益'],350000)
            self.assertEqual(chosen['payload']['合計']['仕入原価'],sum(s['net_total'] for s in sources))
            self.assertEqual(rows,before)
            for old,new in zip(rows,chosen['payload']['計算明細']):
                for field in ('数量','原価単価','原価金額','見積元','原本ID'):self.assertEqual(old[field],new[field])
                if old['原価金額']<0:self.assertEqual(new['見積金額'],old['原価金額'])
            result=build_approved_estimate(chosen,approve(chosen,'複数社検証'))
            self.assertTrue(result.ok,result.errors)
            self.assertTrue(result.pdf_data.startswith(b'%PDF'));self.assertTrue(result.data.startswith(b'PK'))
            if output:
                (folder/'combined.pdf').write_bytes(result.pdf_data)
                (folder/'combined.xlsx').write_bytes(result.data)
                (folder/'result.json').write_text(json.dumps({'company_count':count,'source_count':len(sources),'source_totals':{s['name']:s['net_total'] for s in sources},'totals':chosen['payload']['合計'],'rows':chosen['payload']['計算明細']},ensure_ascii=False,indent=2))
            changed=copy.deepcopy(config);changed['limit']-=1
            state={'review_pricing_choice':choice,'_work_draft_inputs':{'review_pricing_choice':choice}}
            self.assertTrue(reconcile_inputs(state,rows,targets,.1,0,changed))
            self.assertNotIn('review_pricing_choice',state)
            self.assertNotIn('review_pricing_choice',state['_work_draft_inputs'])
            no=build_draft(rows,copy.deepcopy(rows),sources,targets,0,meta,pricing_choice=choice_for(pending['pricing_options'],False))
            self.assertEqual(no['payload']['値引き'],0)
            self.assertEqual(no['payload']['合計']['粗利益'],350000)
            scoped=[Target('業者別',f'検証{i+1}社','上乗せ額',70000) for i in range(count)]
            scoped_draft=build_draft(rows,copy.deepcopy(rows),sources,scoped,0,meta)
            self.assertIsNone(scoped_draft['pricing_options']['proposal'])
            self.assertIn('全体',scoped_draft['pricing_options']['proposal_error'])
            self.assertEqual(scoped_draft['payload']['合計']['粗利益'],count*70000)
            for index in range(count):
                company=[r for r in scoped_draft['payload']['計算明細'] if r['見積元']==f'検証{index+1}社']
                self.assertEqual(sum(r['見積金額']-r['原価金額'] for r in company),70000)


    def test_three_companies(self):self.run_case(3)
    def test_five_companies(self):self.run_case(5)

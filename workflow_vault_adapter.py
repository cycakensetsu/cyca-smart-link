"""Owner/company-scoped bridge to existing approved quote PDFs and invoice registry."""
import json
import re
from invoice_documents import invoice_filename
from estimate_review import digest, verify_approval


def make_resolvers(vault_factory, database):
    def estimate(company, approval_id):
        if not re.fullmatch('[a-f0-9]{64}',str(approval_id)): raise KeyError()
        for name,_,read in vault_factory(company).entries():
            if name.startswith('approvals/'):
                value=json.loads(read())
                if value.get('id')==approval_id:
                    verify_approval({'payload':value['payload'],'fingerprint':digest(value['payload']),'unresolved':[]},value)
                    return value
        raise KeyError('承認済み見積がありません。')

    def document(company, document_id):
        with database.connect() as db:
            row=db.execute('SELECT payload,pdf FROM invoices WHERE owner=? AND fingerprint=?',(company,document_id)).fetchone()
        if row:
            invoice=json.loads(row['payload'])
            return {'filename':invoice_filename(invoice),'pdf':row['pdf']}
        approval=estimate(company,document_id)
        vault=vault_factory(company)
        files=[name for name,_,_ in vault.entries() if name.startswith('exports/'+approval['id']+'/') and name.endswith('.pdf')]
        if len(files)!=1:
            raise ValueError('送信する確定PDFを一意に確認できません。')
        header=approval['payload']['表題']
        title=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',str(header.get('工事名称','工事')))[:80]
        from invoice_documents import _date
        day=_date(header.get('見積日')).replace('-','')
        return {'filename':f'{title}見積書_{day}.pdf','pdf':vault.get_entry(files[0])}
    return estimate,document

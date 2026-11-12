"""Optional authenticated HTTP adapter; never starts or exposes itself on import.
Deploy only behind HTTPS with server-configured bearer token hashes and owner map.
resolve_estimate(owner, approval_id) must load from that owner's existing vault.
"""
import hashlib
import hmac
from invoice_documents import build_invoice, invoice_fingerprint, render_invoice_pdf
from document_workflow_service import DocumentWorkflowService


def create_app(*, store, token_owners, resolve_estimate, issuer, resolve_document=None, sender=None, transport=None, actor_companies=None, actor_permissions=None):
    from fastapi import FastAPI, Depends, HTTPException
    from fastapi.security import HTTPBearer
    from fastapi.responses import Response
    auth = HTTPBearer(auto_error=False)
    if not token_owners or any(len(digest)!=64 or not owner for digest,owner in token_owners.items()):
        raise ValueError('API認証が未設定です。')
    app=FastAPI(title='CYCA Document Workflow',docs_url=None,redoc_url=None,openapi_url=None)

    def owner(credentials=Depends(auth)):
        if credentials is None or credentials.scheme.lower()!='bearer':
            raise HTTPException(401,'認証が必要です。')
        supplied=hashlib.sha256(credentials.credentials.encode()).hexdigest()
        for expected,identity in token_owners.items():
            if hmac.compare_digest(supplied,expected): return identity
        raise HTTPException(401,'認証できません。')

    def scope(identity):
        if actor_companies is None: return identity
        if identity not in actor_companies or not actor_companies[identity]:
            raise HTTPException(403,'会社の利用権限がありません。')
        return actor_companies[identity]

    def workflow(identity):
        permissions=None if actor_permissions is None else actor_permissions.get(identity,[])
        return DocumentWorkflowService(store,identity,transport,company_id=scope(identity),permissions=permissions)

    def require(identity, action):
        try: workflow(identity).require(action)
        except ValueError: raise HTTPException(403,'この操作を行う権限がありません。')

    @app.post('/invoices/preview')
    def preview(request:dict,identity=Depends(owner)):
        require(identity,'prepare')
        try:
            invoice=build_invoice(resolve_estimate(scope(identity),request['estimate_approval_id']),
                                  invoice_number=request['invoice_number'],issue_date=request['issue_date'],
                                  due_date=request['due_date'],issuer=issuer,
                                  estimate_date=request.get('estimate_date'))
            # No arbitrary invoice content is accepted at finalization: regenerate from source.
            return {'invoice':invoice,'fingerprint':invoice_fingerprint(invoice)}
        except (KeyError,ValueError): raise HTTPException(400,'承認済み見積・日付・入力を確認してください。')

    @app.post('/invoices/finalize')
    def finalize(request:dict,identity=Depends(owner)):
        require(identity,'approve')
        result=preview(request,identity)
        if request.get('confirmed') is not True or request.get('fingerprint')!=result['fingerprint']:
            raise HTTPException(409,'最新の請求書内容の承認が必要です。')
        from invoice_registry import InvoiceRegistry
        try: pdf=InvoiceRegistry(store).finalize(scope(identity),result['invoice'],result['fingerprint'],identity)
        except ValueError: raise HTTPException(409,'請求書番号または承認を確認してください。')
        return Response(pdf,media_type='application/pdf')

    @app.post('/deliveries/prepare')
    def prepare(request:dict,identity=Depends(owner)):
        if resolve_document is None or not sender:
            raise HTTPException(503,'確定PDF保管先または送信元が未設定です。')
        try:
            document=resolve_document(scope(identity),request['document_id'])
            return workflow(identity).prepare_email(request['request_key'],sender=sender,
                recipient=request['recipient'],subject=request['subject'],body=request['body'],
                filename=document['filename'],pdf=document['pdf'],document_id=request['document_id'],
                thread_id=request.get('thread_id'),reply_message_id=request.get('reply_message_id'))
        except (KeyError,ValueError): raise HTTPException(400,'確定PDF・宛先・本文を確認してください。')

    @app.post('/invoices/preview/pdf')
    def preview_pdf(request:dict,identity=Depends(owner)):
        return Response(render_invoice_pdf(preview(request,identity)['invoice']),media_type='application/pdf')

    @app.get('/deliveries')
    def history(identity=Depends(owner)):
        require(identity,'read')
        return store.history(scope(identity))

    @app.get('/deliveries/{operation_id}')
    def review(operation_id:str,identity=Depends(owner)):
        try: return workflow(identity).review(operation_id)
        except KeyError: raise HTTPException(404,'送信記録がありません。')

    @app.post('/deliveries/{operation_id}/approve')
    def approve(operation_id:str,request:dict,identity=Depends(owner)):
        if request.get('confirmed') is not True: raise HTTPException(409,'本人の承認が必要です。')
        try: return workflow(identity).approve_email(operation_id,request.get('fingerprint'))
        except (ValueError,KeyError): raise HTTPException(409,'最新の送信内容を確認してください。')

    @app.post('/deliveries/{operation_id}/send')
    def send(operation_id:str,identity=Depends(owner)):
        try: return workflow(identity).send_email(operation_id)
        except (ValueError,RuntimeError,KeyError): raise HTTPException(409,'送信未完了または結果未確認です。状態を確認してください。')

    @app.post('/deliveries/{operation_id}/reconcile')
    def reconcile(operation_id:str,identity=Depends(owner)):
        try: return workflow(identity).reconcile_email(operation_id)
        except (ValueError,RuntimeError,KeyError): raise HTTPException(409,'照合できません。再送は行っていません。')
    return app

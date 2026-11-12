"""Durable review/send boundary. No network is used without an explicit transport.
Use on a persistent server volume; Streamlit ephemeral disk is not production storage.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage


def now():
    return datetime.now(timezone.utc).isoformat()


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value):
        raise ValueError('メールアドレスを確認してください。')
    return value


class DeliveryStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS deliveries
                (id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
                 payload TEXT NOT NULL, pdf BLOB NOT NULL, fingerprint TEXT NOT NULL,
                 state TEXT NOT NULL, actor TEXT, approved_at TEXT, gmail_id TEXT,
                 thread_id TEXT, error TEXT, updated_at TEXT NOT NULL,
                 UNIQUE(owner, request_key))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def prepare(self, owner, request_key, *, sender, recipient, subject, body,
                filename, pdf, document_id, thread_id=None, reply_message_id=None):
        if not owner or not request_key or not document_id:
            raise ValueError('利用者・操作ID・帳票IDが必要です。')
        address(sender); address(recipient)
        if (not subject or any(c in subject for c in '\r\n') or not body
                or not filename.endswith('.pdf') or any(c in filename for c in '/\\\r\n')
                or not pdf.startswith(b'%PDF-')):
            raise ValueError('本文・件名・PDFを確認してください。')
        if reply_message_id and any(c in reply_message_id for c in '\r\n'):
            raise ValueError('返信先メッセージIDを確認してください。')
        payload = dict(sender=sender, recipient=recipient, subject=subject, body=body,
                       filename=filename, document_id=document_id, thread_id=thread_id,
                       reply_message_id=reply_message_id, pdf_sha256=hashlib.sha256(pdf).hexdigest())
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM deliveries WHERE owner=? AND request_key=?',
                             (owner, request_key)).fetchone()
            if row:
                if row['fingerprint'] != fingerprint:
                    raise ValueError('同じ操作IDの内容が変わっています。新規確認が必要です。')
                return row['id']
            # A fresh request UUID must never bypass an in-flight or sent business action.
            # Compare source identity OR attachment hash: changed wording, filenames or
            # regenerated request IDs are not authorization to resend the same document.
            candidates = db.execute("SELECT id,payload,fingerprint,state FROM deliveries WHERE owner=? AND state != 'cancelled'", (owner,)).fetchall()
            for existing in candidates:
                prior=json.loads(existing['payload'])
                same_recipient=prior['recipient'].casefold()==recipient.casefold()
                same_document=(prior['document_id']==document_id or prior['pdf_sha256']==payload['pdf_sha256'])
                if same_recipient and same_document:
                    if existing['fingerprint']==fingerprint:
                        return existing['id']
                    raise ValueError('同じ書類・宛先の送信記録があります。既存記録を確認してください。新しい操作IDでの再送はできません。')
            key = uuid.uuid4().hex
            db.execute('INSERT INTO deliveries (id,owner,request_key,payload,pdf,fingerprint,state,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (key, owner, request_key, encoded, pdf, fingerprint, 'review', now()))
            return key

    def get(self, owner, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM deliveries WHERE id=? AND owner=?', (key,owner)).fetchone()
        if not row:
            raise KeyError('送信記録がありません。')
        result = dict(row); result['payload'] = json.loads(result['payload'])
        return result

    def history(self, owner, document_id=None):
        with self.connect() as db:
            rows = db.execute('SELECT id,payload,state,gmail_id,updated_at FROM deliveries WHERE owner=? ORDER BY updated_at DESC LIMIT 100', (owner,)).fetchall()
        results=[]
        for row in rows:
            item=dict(row); item['payload']=json.loads(item['payload'])
            if document_id is None or item['payload']['document_id']==document_id:
                results.append(item)
        return results

    def cancel_unsent(self, owner, key):
        with self.connect() as db:
            changed=db.execute("UPDATE deliveries SET state='cancelled',updated_at=? WHERE owner=? AND id=? AND state IN ('review','approved')", (now(),owner,key)).rowcount
        if changed!=1:
            raise ValueError('送信開始済みの記録は取り消して再送できません。')

    def approve(self, owner, key, fingerprint, actor):
        if not actor:
            raise ValueError('承認者が必要です。')
        with self.connect() as db:
            changed = db.execute("UPDATE deliveries SET state='approved',actor=?,approved_at=?,updated_at=? WHERE id=? AND owner=? AND fingerprint=? AND state='review'",
                                 (actor,now(),now(),key,owner,fingerprint)).rowcount
        if changed != 1:
            raise ValueError('最新の未送信内容を確認してください。')

    def send(self, owner, key, transport):
        row = self.get(owner,key)
        if row['state'] == 'sent':
            return row
        # Transactional claim prevents double-clicks/concurrent workers from sending twice.
        with self.connect() as db:
            claimed = db.execute("UPDATE deliveries SET state='sending',updated_at=? WHERE id=? AND owner=? AND state='approved'", (now(),key,owner)).rowcount
        if claimed != 1:
            raise ValueError('送信中・確認待ちの記録は再送できません。')
        try:
            result = transport.send(row['payload'], row['pdf'], key)
            if not isinstance(result, dict) or not result.get('id'):
                raise RuntimeError('Gmailの送信結果を確認できません。')
            with self.connect() as db:
                db.execute("UPDATE deliveries SET state='sent',gmail_id=?,thread_id=?,updated_at=? WHERE id=? AND owner=?", (result['id'],result.get('threadId'),now(),key,owner))
        except Exception:
            # Including response loss: never silently retry. Do not persist secret-bearing errors.
            with self.connect() as db:
                db.execute("UPDATE deliveries SET state='unknown',error=?,updated_at=? WHERE id=? AND owner=?", ('送信結果未確認。Gmail送信済みとの照合が必要です。',now(),key,owner))
            raise RuntimeError('送信結果が不明です。自動再送は行いません。') from None
        return self.get(owner,key)

    def reconcile(self, owner, key, transport):
        row = self.get(owner,key)
        if row['state'] not in ('sending','unknown'):
            return row
        results = transport.find(key, row['payload']['sender'])
        if len(results) == 1 and results[0].get('id'):
            with self.connect() as db:
                db.execute("UPDATE deliveries SET state='sent',gmail_id=?,thread_id=?,error=NULL,updated_at=? WHERE id=? AND owner=? AND state IN ('sending','unknown')", (results[0]['id'],results[0].get('threadId'),now(),key,owner))
        # Search returning zero is not proof of non-send; keep blocked.
        return self.get(owner,key)


class GmailTransport:
    """Inject an authenticated Gmail v1 service. OAuth provisioning is deliberately separate."""
    def __init__(self, service):
        self.service = service

    def send(self, payload, pdf, operation_id):
        import base64
        profile = self.service.users().getProfile(userId='me').execute()
        if profile.get('emailAddress','').lower() != payload['sender'].lower():
            raise ValueError('Gmail送信元が一致しません。')
        message = EmailMessage()
        message['From'] = payload['sender']; message['To'] = payload['recipient']
        message['Subject'] = payload['subject']
        message['Message-ID'] = f'<cyca-{operation_id}@{payload["sender"].split("@")[1]}>'
        if payload.get('reply_message_id'):
            message['In-Reply-To'] = payload['reply_message_id']
            message['References'] = payload['reply_message_id']
        message.set_content(payload['body'])
        message.add_attachment(pdf, maintype='application', subtype='pdf', filename=payload['filename'])
        request = {'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()}
        if payload.get('thread_id'):
            request['threadId'] = payload['thread_id']
        return self.service.users().messages().send(userId='me',body=request).execute(num_retries=0)

    def find(self, operation_id, sender):
        if not re.fullmatch('[a-f0-9]{32}', operation_id):
            raise ValueError('操作IDが不正です。')
        address(sender)
        message_id = f'cyca-{operation_id}@{sender.split("@")[1]}'
        data = self.service.users().messages().list(userId='me', q=f'in:sent rfc822msgid:{message_id}', maxResults=2).execute()
        return data.get('messages', [])

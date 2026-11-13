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


def delivery_fingerprint(payload):
    """The canonical fingerprint used by the outbox and Gmail audit header."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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
        fingerprint = delivery_fingerprint(payload)
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
        if delivery_fingerprint(row['payload']) != row['fingerprint']:
            raise ValueError('保存した送信内容の整合性を確認できません。')
        results = transport.find(key, row['payload']['sender'], payload=row['payload'], approved_at=row['approved_at'])
        if len(results) == 1 and results[0].get('id'):
            with self.connect() as db:
                db.execute("UPDATE deliveries SET state='sent',gmail_id=?,thread_id=?,error=NULL,updated_at=? WHERE id=? AND owner=? AND state IN ('sending','unknown')", (results[0]['id'],results[0].get('threadId'),now(),key,owner))
        # Search returning zero is not proof of non-send; keep blocked.
        return self.get(owner,key)


class GmailTransport:
    """Inject an authenticated Gmail v1 service. OAuth provisioning is deliberately separate."""
    def __init__(self, service):
        self.service = service

    def check_connection(self, sender):
        """Read only the authenticated profile, never list or send messages."""
        try:
            address(sender)
            profile = self.service.users().getProfile(userId='me').execute(num_retries=0)
            actual = profile.get('emailAddress')
            if not isinstance(actual,str) or actual.casefold()!=sender.casefold():
                raise ValueError()
            return sender
        except Exception:
            raise ValueError('Gmail接続を確認できません。認可と送信元の設定を確認してください。') from None

    def send(self, payload, pdf, operation_id):
        import base64
        profile = self.service.users().getProfile(userId='me').execute()
        if profile.get('emailAddress','').lower() != payload['sender'].lower():
            raise ValueError('Gmail送信元が一致しません。')
        message = EmailMessage()
        message['From'] = payload['sender']; message['To'] = payload['recipient']
        message['Subject'] = payload['subject']
        message['X-CYCA-Operation-ID'] = operation_id
        message['X-CYCA-Payload-SHA256'] = delivery_fingerprint(payload)
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

    def find(self, operation_id, sender, *, payload, approved_at):
        """Bounded, read-only sent scan. A partial scan can NEVER prove a send.

        Gmail may rewrite Message-ID. New sends carry an exact operation marker
        and the outbox fingerprint. Old messages require the original Message-ID.
        Metadata does not prove attachment bytes; missing markers remain unknown.
        """
        from email.utils import getaddresses
        from email.header import decode_header, make_header
        if not re.fullmatch('[a-f0-9]{32}', operation_id):
            raise ValueError('操作IDが不正です。')
        address(sender)
        if not isinstance(payload,dict) or payload.get('sender','').casefold() != sender.casefold():
            raise ValueError('保存した送信元が一致しません。')
        address(payload.get('recipient'))
        try:
            approved = datetime.fromisoformat(approved_at)
            if approved.tzinfo is None:
                raise ValueError()
            epoch = int(approved.astimezone(timezone.utc).timestamp())
            current = int(datetime.now(timezone.utc).timestamp())
            if epoch <= 0 or epoch > current + 300:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError('送信承認日時を確認できません。再送は行いません。') from None
        fingerprint = delivery_fingerprint(payload)
        profile = self.service.users().getProfile(userId='me').execute()
        if profile.get('emailAddress','').casefold() != sender.casefold():
            raise ValueError('Gmail照合元が一致しません。')
        old_message_id = f'<cyca-{operation_id}@{sender.split("@")[1]}>'
        fields = ['X-CYCA-Operation-ID','X-CYCA-Payload-SHA256','Message-ID','From','To','Cc','Bcc','Subject']
        def matches(message):
            if (not isinstance(message,dict) or not isinstance(message.get('labelIds'),list)
                    or not isinstance(message.get('payload'),dict)
                    or not isinstance(message['payload'].get('headers'),list)):
                raise ValueError('Gmail候補のメタデータを確認できません。')
            if any(not isinstance(h,dict) or not isinstance(h.get('name'),str) or not isinstance(h.get('value'),str)
                   for h in message['payload']['headers']):
                raise ValueError('Gmail候補のヘッダーを確認できません。')
            try:
                internal = int(message['internalDate']) // 1000
                if not epoch-300 <= internal <= current+300: return False
                if 'SENT' not in message['labelIds']: return False
                headers = {}
                for header in message['payload']['headers']:
                    headers.setdefault(header['name'].casefold(),[]).append(header['value'])
                def single(name):
                    values = headers.get(name.casefold(),[])
                    return values[0] if len(values)==1 and isinstance(values[0],str) else None
                def mailbox(name, expected):
                    value = single(name)
                    if value is None: return False
                    addresses = getaddresses([value])
                    return len(addresses)==1 and addresses[0][1].casefold()==expected.casefold()
                if not mailbox('From',sender) or not mailbox('To',payload['recipient']): return False
                if headers.get('cc') or headers.get('bcc'): return False
                subject = single('Subject')
                if subject is None or str(make_header(decode_header(subject))) != payload['subject']: return False
                marker = single('X-CYCA-Operation-ID')
                proof = single('X-CYCA-Payload-SHA256')
                # RFC folding can leave outer whitespace in metadata values.
                # Strip only edges, never internal whitespace or duplicate fields.
                marker = marker.strip() if marker is not None else None
                proof = proof.strip() if proof is not None else None
                if marker is not None or proof is not None or 'x-cyca-operation-id' in headers or 'x-cyca-payload-sha256' in headers:
                    return marker==operation_id and proof==fingerprint
                # Legacy path: never accept rewritten Message-ID or subject alone.
                return single('Message-ID')==old_message_id
            except (KeyError,TypeError,ValueError,LookupError):
                raise ValueError('Gmail候補のメタデータ解析が未完了です。') from None
        found = {}; seen = set(); token = None; page_tokens = set()
        for _ in range(5):
            # Date-only query avoids interpolation of recipient-supplied search syntax.
            args = dict(userId='me',labelIds=['SENT'],q=f'after:{max(0,epoch-300)} before:{current+301}',maxResults=100)
            if token: args['pageToken']=token
            page = self.service.users().messages().list(**args).execute(num_retries=0)
            if not isinstance(page,dict) or not isinstance(page.get('messages',[]),list):
                raise ValueError('Gmail候補の照合が未完了です。')
            if len(page.get('messages',[])) > 100:
                raise ValueError('Gmail候補がページ上限を超えました。')
            for entry in page.get('messages',[]):
                message_id = entry.get('id') if isinstance(entry,dict) else None
                if not isinstance(message_id,str) or not message_id:
                    raise ValueError('Gmail候補の照合が未完了です。')
                if message_id in seen: continue
                seen.add(message_id)
                message = self.service.users().messages().get(userId='me',id=message_id,format='metadata',metadataHeaders=fields).execute(num_retries=0)
                if not isinstance(message,dict) or message.get('id') != message_id:
                    raise ValueError('Gmail候補のIDが一致しません。')
                if matches(message):
                    found[message_id]={'id':message_id,'threadId':message.get('threadId')}
            token = page.get('nextPageToken')
            if not token: return list(found.values())
            if not isinstance(token,str) or token in page_tokens:
                raise ValueError('Gmail候補のページ確認が未完了です。')
            page_tokens.add(token)
        # Even one matching result is insufficient when later pages were not read.
        raise ValueError('Gmail候補が検索上限を超えました。送信結果は未確認のままです。')

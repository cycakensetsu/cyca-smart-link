"""Application facade shared by Streamlit and a future authenticated bot adapter.
No public HTTP endpoint, no caller-supplied identity escalation, no implicit sends.
"""
from document_delivery import DeliveryStore


class DocumentWorkflowService:
    def __init__(self, store, owner, transport=None, company_id=None, permissions=None, sender=None):
        if not owner:
            raise ValueError('認証済み利用者が必要です。')
        self.store = store
        self.owner = owner
        self.scope = company_id or owner
        self.transport = transport
        self.sender = sender  # Trusted server mapping; None only for legacy direct callers.
        self.permissions = set(permissions) if permissions is not None else {'prepare','approve','send','read'}

    def require(self, action):
        if action not in self.permissions:
            raise ValueError('この操作を行う権限がありません。')

    def validate_sender(self, sender):
        if self.sender is not None and (not self.sender or not isinstance(sender, str) or sender.casefold() != self.sender.casefold()):
            raise ValueError("この利用者のGmail送信元と一致しません。")

    def validate_mailbox(self, operation_id):
        self.validate_sender(self.store.get(self.scope, operation_id)["payload"]["sender"])

    def prepare_email(self, request_key, **document_and_message):
        self.require('prepare')
        self.require('read')
        self.validate_sender(document_and_message.get('sender'))
        key = self.store.prepare(self.scope, request_key, **document_and_message)
        self.validate_mailbox(key)
        return self.review(key)

    def review(self, operation_id):
        self.require('read')
        row = self.store.get(self.scope, operation_id)
        return {key:row[key] for key in ('id','payload','fingerprint','state','gmail_id','thread_id','error')}

    def approve_email(self, operation_id, fingerprint):
        # An adapter must call this ONLY following the owner's explicit approval.
        self.require('approve')
        self.require('read')
        self.store.approve(self.scope, operation_id, fingerprint, self.owner)
        return self.review(operation_id)

    def cancel_email(self, operation_id):
        self.require('approve')
        self.require('read')
        self.store.cancel_unsent(self.scope,operation_id)
        return self.review(operation_id)

    def check_gmail_connection(self):
        self.require('read')
        if self.transport is None or not self.sender:
            raise ValueError('Gmail接続が未設定です。')
        return self.transport.check_connection(self.sender)

    def send_email(self, operation_id):
        self.require('send')
        self.require('read')
        if self.transport is None:
            raise ValueError('Gmail連携が未設定です。送信していません。')
        self.validate_mailbox(operation_id)
        self.store.send(self.scope, operation_id, self.transport)
        return self.review(operation_id)

    def reconcile_email(self, operation_id):
        self.require('read')
        if self.transport is None:
            raise ValueError('Gmail連携が未設定です。')
        self.validate_mailbox(operation_id)
        self.store.reconcile(self.scope, operation_id, self.transport)
        return self.review(operation_id)

"""Real SDK against a local HTTP contract fixture, never a live Supabase project."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from io import BytesIO
from zipfile import ZipFile

import httpx
from storage3 import SyncStorageClient

from estimate_review import OriginalVault, canonical, digest
from saved_quotes import completed_quotes
from shared_storage import SharedVault, SupabaseObjects, configuration, object_path, configured_vault
from shared_storage_migration import migrate_archive
from test_work_drafts import working
from work_drafts import save_draft, available_drafts, restore_draft
from vault_archive import archive_bytes, restore_archive

URL = 'https://syntheticprojectfixture.supabase.co'
KEY = 'sb_secret_synthetic_local_contract_test_only'
BUCKET = 'cyca-estimates'


def archive_payloads(data):
    with ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class StorageHTTPFixture:
    """Independent durable service fixture; separate app hosts share only HTTP."""
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.public=False;self.outage=False
        self.lose_next_upload_response=False;self.requests=[]

    def response(self, request):
        with self.lock:
            path=request.url.path.removeprefix('/storage/v1/')
            self.requests.append((request.method,path))
            def error(status, code):
                return httpx.Response(status,json={'statusCode':str(status),'error':code,
                                                   'message':'synthetic error '+KEY})
            if request.headers.get('apikey') != KEY:
                return error(403,'AccessDenied')
            if self.outage:return error(503,'InternalError')
            if path=='bucket/'+BUCKET:
                return httpx.Response(200,json=dict(id=BUCKET,name=BUCKET,owner='fixture',public=self.public,
                    created_at='2026-11-02T00:00:00Z',updated_at='2026-11-02T00:00:00Z',
                    file_size_limit=None,allowed_mime_types=None))
            if path=='object/list/'+BUCKET:
                args=json.loads(request.content);folder=self.root/args['prefix']
                names=sorted(p for p in folder.iterdir() if p.is_file()) if folder.exists() else []
                return httpx.Response(200,json=[{'name':p.name,'id':hashlib.sha256(p.name.encode()).hexdigest(),
                    'metadata':{'size':p.stat().st_size}} for p in names[args['offset']:args['offset']+args['limit']]])
            prefix='object/'+BUCKET+'/'
            if not path.startswith(prefix):return error(404,'NoSuchKey')
            target=self.root/path[len(prefix):]
            if request.method=='GET':
                return httpx.Response(200,content=target.read_bytes()) if target.exists() else error(404,'NoSuchKey')
            if request.method=='POST':
                if request.headers.get('x-upsert') != 'false':return error(400,'UnsafeOverwrite')
                if target.exists():return error(409,'Duplicate')
                message=BytesParser(policy=default).parsebytes(
                    b'Content-Type: '+request.headers['content-type'].encode()+b'\r\n\r\n'+request.content)
                content=next(p.get_payload(decode=True) for p in message.iter_parts()
                             if p.get_param('name',header='content-disposition')=='file')
                target.parent.mkdir(exist_ok=True);target.write_bytes(content)
                if self.lose_next_upload_response:
                    self.lose_next_upload_response=False
                    raise httpx.ReadError('lost reply '+KEY,request=request)
                return httpx.Response(200,json={'Key':BUCKET+'/'+path[len(prefix):],'Id':'fixture-object'})
            return error(405,'MethodNotAllowed')

    def objects(self):
        client=SyncStorageClient(URL+'/storage/v1/',{'apikey':KEY,'Authorization':'Bearer '+KEY},
            http_client=httpx.Client(transport=httpx.MockTransport(self.response)))
        return SupabaseObjects(URL,KEY,BUCKET,client=client)


class SharedStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.service=StorageHTTPFixture(self.root/'service')

    def host(self, name, owner='owner'):
        return SharedVault(self.root/name,owner,self.service.objects())

    def test_configuration_is_opt_in_and_incomplete_or_public_keys_fail_closed(self):
        self.assertIsNone(configuration({},{}))
        local = configured_vault(self.root/'old','owner',{},{} )
        self.assertEqual(local.root, self.root/'old'/hashlib.sha256(b'owner').hexdigest())
        self.assertFalse(hasattr(local, 'objects'))
        for config in [{'url':URL},{'url':'http://localhost','service_key':KEY},
                       {'url':URL,'service_key':'sb_publishable_never_allowed'},
                       {'url':URL+'/path','service_key':KEY},{'url':URL,'service_key':KEY,'bucket':'../other'}]:
            with self.assertRaises(ValueError):configuration(config,{})
        self.assertEqual(configuration({'url':URL,'service_key':KEY},{}),(URL,KEY,BUCKET))
        with patch('shared_storage.backend') as connect:
            with self.assertRaises(ValueError):configured_vault(self.root/'bad','owner',{'url':URL},{})
            connect.assert_not_called()

    def test_two_cold_hosts_restore_corrected_work_and_preexisting_outputs_without_local_transfer(self):
        a=self.host('host-A');state=working(a);key=save_draft(state,a)
        # Finished shelf uses a genuine internal approval, with no fresh generation.
        from estimate_review import approve
        payload={'表題':{'工事名称':'保存済みの架空工事'},'確認明細':[], '合計':{'税込':220}}
        approval=approve({'unresolved':[],'fingerprint':digest(payload),'payload':payload},'owner')
        a.snapshot('approvals',approval);a.export(approval,'pdf',b'synthetic completed PDF');a.export(approval,'xlsx',b'synthetic completed XLSX')
        b=self.host('host-B');self.assertEqual(list(b.root.rglob('*.json')),[])
        self.assertEqual([v['id'] for v in available_drafts(b)],[key])
        target={'logged_in':True,'username':'owner','_auth_signature':'current-login'}
        restore_draft(target,b,key)
        for k in ('review_customer','review_project','_review_chat','_review_assistant_rows'):
            self.assertEqual(target[k],state[k])
        self.assertEqual(target['_auth_signature'],'current-login')
        self.assertFalse(target['_review_export_ready'])
        quote=completed_quotes(b)[0];self.assertEqual(quote['title'],'保存済みの架空工事')
        self.assertEqual(b.get_entry(quote['files']['pdf'][0]),b'synthetic completed PDF')
        self.assertEqual(b.get_entry(quote['files']['xlsx'][0]),b'synthetic completed XLSX')
        # Replace both the application cache AND service process, retaining only
        # durable service files. The new host must find the same work.
        self.service=StorageHTTPFixture(self.root/'service')
        c=self.host('replacement-host')
        self.assertEqual([v['id'] for v in available_drafts(c)],[key])
        self.assertEqual(archive_payloads(archive_bytes(c)),archive_payloads(archive_bytes(a)))

    def test_same_work_new_revision_is_visible_despite_an_old_host_local_head(self):
        a=self.host('A');state=working(a);first=save_draft(state,a)
        b=self.host('B');restore_draft({},b,first)
        state['review_project']='新しい確定工事名'
        latest=save_draft(state,b,datetime.now(timezone.utc)+timedelta(seconds=1))
        self.assertNotEqual(first,latest)
        self.assertEqual([v['id'] for v in available_drafts(a)],[latest])

    def test_ten_independent_hosts_and_a_different_login_never_mix_work(self):
        a=self.host('seed');key=save_draft(working(a),a)
        def update(i):
            store=self.host('independent-'+str(i));state={'username':'owner'}
            restore_draft(state,store,key);state['review_project']='端末'+str(i)
            return save_draft(state,store)
        with ThreadPoolExecutor(max_workers=10) as pool:keys=list(pool.map(update,range(10)))
        self.assertEqual(len(set(keys)),10)
        all_drafts=available_drafts(self.host('cold-reader'))
        self.assertEqual(len(all_drafts),11)
        self.assertEqual(len({v['work_id'] for v in all_drafts}),11)
        other=self.host('other','different-login')
        self.assertEqual(available_drafts(other),[])
        with self.assertRaises(ValueError):restore_draft({},other,key)
        self.assertEqual(completed_quotes(other),[])

    def test_failed_remote_write_does_not_claim_saved_or_leave_confirmed_local_cache(self):
        a=self.host('A');state=working(a);original=save_draft(state,a)
        previous=state['_work_draft_saved_at'];state['review_project']='未保存の変更'
        self.service.outage=True
        with self.assertRaises(OSError) as err:save_draft(state,a)
        self.assertNotIn(KEY,str(err.exception));self.assertEqual(state['_work_draft_record'],original)
        self.assertEqual(state['_work_draft_saved_at'],previous)
        self.service.outage=False
        self.assertEqual(len(available_drafts(self.host('fresh'))),1)
        self.assertNotEqual(save_draft(state,a),original)

    def test_lost_ack_can_retry_and_duplicate_never_overwrites_a_corrupt_remote_record(self):
        a=self.host('A');data=b'synthetic immutable source';sid=hashlib.sha256(data).hexdigest();name=f'originals/{sid}/original'
        self.service.lose_next_upload_response=True
        with self.assertRaises(OSError):a._put(name,data)
        self.assertFalse((a.root/name).exists())
        a._put(name,data);self.assertEqual(self.host('B').read(sid),data)
        remote=self.service.root/object_path(a.root.name,name);remote.write_bytes(b'corrupt record')
        with self.assertRaises(ValueError):a._put(name,data)
        self.assertEqual(remote.read_bytes(),b'corrupt record')

    def test_outage_is_not_an_empty_list_and_public_bucket_stops_all_transfers(self):
        a=self.host('A');state=working(a);save_draft(state,a)
        self.service.outage=True
        with self.assertRaises(OSError):available_drafts(a)
        self.service.outage=False;self.service.public=True
        before=len(self.service.requests)
        with self.assertRaises(OSError):available_drafts(a)
        with self.assertRaises(OSError):a.preserve('new.pdf',b'new source')
        self.assertTrue(all(path.startswith('bucket/') for _,path in self.service.requests[before:]))

    def test_paginated_list_preserves_all_records_and_owner_path_cannot_escape(self):
        a=self.host('A')
        for i in range(205):a.snapshot('uploads',{'index':i})
        b=self.host('B');self.assertEqual(len(list(b.entries())),205)
        self.assertTrue(all(b.get_entry(n) for n,_,_ in b.entries()))
        for owner,name in [('..','uploads/'+'a'*64+'.json'),(a.root.name,'../other'),(a.root.name,'drafts/x.json')]:
            with self.assertRaises(ValueError):object_path(owner,name)

    def test_migration_preflights_before_any_upload_and_is_retryable_and_additive(self):
        old=OriginalVault(self.root/'legacy','owner');state=working(old);save_draft(state,old)
        data=archive_bytes(old);target=self.host('target')
        self.assertFalse(migrate_archive(data,target)['applied']);self.assertEqual(self.service.requests,[])
        with self.assertRaises(ValueError):migrate_archive(data,self.host('wrong-owner','other'),apply=True)
        self.assertEqual(self.service.requests,[])
        plan=migrate_archive(data,target,apply=True)
        self.assertTrue(plan['applied']);self.assertEqual(plan['records'],plan['verified_records'])
        before={n:r() for n,_,r in target.entries()}
        migrate_archive(data,target,apply=True)
        cold=self.host('after-migration');self.assertEqual({n:r() for n,_,r in cold.entries()},before)
        self.assertEqual(archive_payloads(archive_bytes(cold)),archive_payloads(data))


if __name__=='__main__':unittest.main()

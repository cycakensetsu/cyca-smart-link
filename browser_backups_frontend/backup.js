const OWNER=/^[a-f0-9]{64}$/,WORK=/^[a-f0-9]{32}$/;
const LIMIT=64*1024*1024;
function valid(p,owner){return p&&p.owner===owner&&OWNER.test(owner)&&WORK.test(p.work)&&OWNER.test(p.revision)&&OWNER.test(p.sha256)&&typeof p.archive==='string'&&p.archive.length<=28*1024*1024&&Number.isSafeInteger(p.revision_us)&&typeof p.title==='string'&&p.title.length<=200;}
function newer(a,b){return !b||a.revision_us>b.revision_us||(a.revision_us===b.revision_us&&a.revision===b.revision);}
function openDB(){return new Promise((resolve,reject)=>{const r=indexedDB.open('cyca-work-recovery-v1',1);r.onupgradeneeded=()=>{const s=r.result.createObjectStore('works',{keyPath:'key'});s.createIndex('owner','owner');};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);r.onblocked=()=>reject(new Error('blocked'));});}
function ownerItems(db,owner){return new Promise((resolve,reject)=>{const t=db.transaction('works','readonly'),r=t.objectStore('works').index('owner').getAll(owner);r.onsuccess=()=>resolve(r.result.filter(v=>valid(v,owner)).sort((a,b)=>b.revision_us-a.revision_us));r.onerror=()=>reject(r.error);});}
function save(db,p){return new Promise((resolve,reject)=>{const t=db.transaction('works','readwrite'),s=t.objectStore('works'),r=s.index('owner').getAll(p.owner);let changed=false;r.onsuccess=()=>{const all=r.result,prev=all.find(v=>v.work===p.work);if(!newer(p,prev))return;const size=all.filter(v=>v.work!==p.work).reduce((n,v)=>n+(v.archive?.length||0),0)+p.archive.length;if(size>LIMIT){t.abort();return;}s.put({...p,key:p.owner+':'+p.work});changed=true;};t.oncomplete=()=>resolve(changed);t.onerror=()=>reject(t.error);t.onabort=()=>reject(t.error||new Error('capacity'));});}
export default function(component){
  const {parentElement:root,data,setStateValue}=component;
  if(!root||!data||!OWNER.test(data.owner))return;
  const status=root.querySelector('#backup-status'),list=root.querySelector('#backup-list'),options=root.querySelector('#recovery-options');
  if(!status||!list)return;
  const context=data.owner+':'+data.session;
  let state=root.cycaBackup;
  if(!state||state.context!==context){state={context,queue:Promise.resolve(),saved:new Set()};root.cycaBackup=state;}
  const current=()=>root.cycaBackup===state;
  state.queue=state.queue.then(async()=>{
    let db;
    try{
      db=await openDB();if(!current())return;
      if(data.packet&&valid(data.packet,data.owner)){
        const p=data.packet;await save(db,p);if(!current())return;
        const verify=(await ownerItems(db,data.owner)).find(v=>v.work===p.work);
        if(!verify||verify.revision!==p.revision)throw new Error('newer work already saved');
        status.textContent='このブラウザにも確定済みの作業を保全しました。';status.dataset.savedRevision=p.revision;
        if(!state.saved.has(p.revision)){state.saved.add(p.revision);setStateValue('event',{kind:'saved',session:data.session,revision:p.revision});}
      }
      const items=await ownerItems(db,data.owner);if(!current())return;
      if(!data.packet)status.textContent=data.head&&items.some(p=>p.revision===data.head)?'このブラウザにも確定済みの作業を保全済みです。':items.length?`このブラウザに${items.length}件の作業が残っています。`:'このブラウザに保存した作業はまだありません。';
      list.replaceChildren();options.open=!data.active&&items.length>0;
      for(const p of items.slice(0,30)){
        const button=document.createElement('button');button.type='button';button.textContent=p.title+'（'+new Date(p.saved_at).toLocaleString('ja-JP')+'）を戻す';button.disabled=!!data.active;
        button.onclick=()=>{if(data.active||!current())return;button.disabled=true;status.textContent='ログイン済みの保管場所へ作業を戻しています。';setStateValue('event',{kind:'restore',session:data.session,packet:p});};list.append(button);
      }
      if(data.active&&items.length){const note=document.createElement('small');note.textContent='別の作業を開くときは、新しい画面でログインして選べます。';list.append(note);}
    }catch(error){if(current())status.textContent='ブラウザへの保全を完了できません。作業データをPCへ保存してください。';}
    finally{if(db)db.close();}
  });
}

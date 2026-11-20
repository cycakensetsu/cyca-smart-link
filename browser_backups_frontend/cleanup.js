const OWNER = /^[a-f0-9]{64}$/;
const WORK = /^[a-f0-9]{32}$/;
const DB = 'cyca-work-recovery-v1';

export function openExisting(idb) {
  return new Promise((resolve, reject) => {
    let absent = false;
    const request = idb.open(DB);
    request.onupgradeneeded = (event) => {
      // A cleanup must not create a new browser database.
      absent = event.oldVersion === 0;
      request.transaction.abort();
    };
    request.onerror = () => absent ? resolve(null) : reject(request.error);
    request.onblocked = () => reject(new Error('blocked'));
    request.onsuccess = () => resolve(request.result);
  });
}

export async function removeOwnerKeys(idb, keyRange, owner) {
  if (typeof owner !== 'string' || !OWNER.test(owner)) throw new Error('invalid owner');
  const db = await openExisting(idb);
  if (!db) return 0;
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction('works', 'readwrite');
      const store = transaction.objectStore('works');
      // Request only keys, not archive values, names or addresses.
      const request = store.index('owner').openKeyCursor(keyRange.only(owner));
      let removed = 0;
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        const key = cursor.primaryKey;
        if (cursor.key === owner && typeof key === 'string'
            && key.startsWith(owner + ':') && WORK.test(key.slice(65))) {
          store.delete(key);
          removed += 1;
        }
        cursor.continue();
      };
      transaction.oncomplete = () => resolve(removed);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('aborted'));
    });
  } finally {
    db.close();
  }
}

export default function(component) {
  const {parentElement: root, data} = component;
  if (!root || typeof data?.owner !== 'string' || !OWNER.test(data.owner)) return;
  const button = root.querySelector('#cleanup-confirm');
  const status = root.querySelector('#cleanup-status');
  if (!button || !status) return;
  const context = {owner: data.owner};
  root.cycaCleanupContext = context;
  button.onclick = async () => {
    if (root.cycaCleanupContext !== context) return;
    button.disabled = true;
    status.textContent = 'このブラウザの旧データを確認しています。';
    try {
      const removed = await removeOwnerKeys(window.indexedDB, window.IDBKeyRange, context.owner);
      if (root.cycaCleanupContext !== context) return;
      status.textContent = removed ? `このブラウザから旧データを${removed}件削除しました。` : '削除対象の旧データはありませんでした。';
    } catch {
      if (root.cycaCleanupContext === context) status.textContent = '旧データの削除を確認できませんでした。';
    } finally {
      if (root.cycaCleanupContext === context) button.disabled = false;
    }
  };
}

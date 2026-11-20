const fs = require('node:fs');
const test = require('node:test');
const assert = require('node:assert/strict');
const script = fs.readFileSync(require('node:path').join(__dirname, 'browser_backups_frontend', 'cleanup.js'));
const moduleImport = import('data:text/javascript;base64,' + script.toString('base64'));

function database(items, {absent = false} = {}) {
  const rows = new Map(items.map(([key, owner]) => [key, owner]));
  let opened = 0, cursorReads = 0, aborted = false;
  const idb = {open(name) {
    assert.equal(name, 'cyca-work-recovery-v1');
    ++opened;
    const request = {transaction:{abort() { aborted = true; }}};
    queueMicrotask(() => {
      if (absent) {
        request.onupgradeneeded({oldVersion: 0});
        request.onerror();
        return;
      }
      request.result = {close() {}, transaction(store, mode) {
        assert.equal(store, 'works');
        assert.equal(mode, 'readwrite');
        const tx = {
          objectStore(name) {
            assert.equal(name, 'works');
            return {
              delete(key) { rows.delete(key); },
              index(name) {
                assert.equal(name, 'owner');
                return {openKeyCursor(owner) {
                  const keys = [...rows].filter(([, keyOwner]) => keyOwner === owner).map(([key]) => key);
                  const cursorRequest = {};
                  function next() {
                    const key = keys.shift();
                    if (!key) {
                      cursorRequest.result = null;
                      cursorRequest.onsuccess();
                      tx.oncomplete();
                      return;
                    }
                    ++cursorReads;
                    cursorRequest.result = {
                      key: owner,
                      primaryKey: key,
                      get value() { throw new Error('archive values must never be read'); },
                      continue() { queueMicrotask(next); },
                    };
                    cursorRequest.onsuccess();
                  }
                  queueMicrotask(next);
                  return cursorRequest;
                }};
              },
            };
          },
        };
        return tx;
      }};
      request.onsuccess();
    });
    return request;
  }};
  return {idb, rows, keyRange:{only:owner=>owner},
          get opened() {return opened;}, get cursorReads(){return cursorReads;},
          get aborted(){return aborted;}};
}

const owner = 'a'.repeat(64), other = 'b'.repeat(64);
const first = owner + ':' + '1'.repeat(32), second = owner + ':' + '2'.repeat(32);
const third = other + ':' + '1'.repeat(32);

test('only the logged-in owner keys are deleted and counted', async () => {
  const {removeOwnerKeys} = await moduleImport;
  const fixture = database([[first, owner],[second, owner],[third, other]]);
  assert.equal(await removeOwnerKeys(fixture.idb, fixture.keyRange, owner), 2);
  assert.deepEqual([...fixture.rows.keys()], [third]);
  assert.equal(fixture.cursorReads, 2);
  const before = fixture.opened;
  await assert.rejects(removeOwnerKeys(fixture.idb, fixture.keyRange, 'incorrect'));
  assert.equal(fixture.opened, before);
});

test('missing DB yields zero without installing a database', async () => {
  const {removeOwnerKeys} = await moduleImport;
  const fixture = database([], {absent:true});
  assert.equal(await removeOwnerKeys(fixture.idb, fixture.keyRange, owner),0);
  assert.equal(fixture.aborted,true);
});

test('component mount itself does not read or delete existing data', async () => {
  const {default:mount} = await moduleImport;
  const fixture = database([[first,owner],[third,other]]);
  global.window={indexedDB:fixture.idb, IDBKeyRange:fixture.keyRange};
  const button = {}, status = {};
  const root = {querySelector(selector){return selector==='#cleanup-confirm'?button:status;}};
  try {
    mount({parentElement:root,data:{owner},setStateValue(){throw new Error('no server transfer');}});
    assert.equal(fixture.opened,0);
    await button.onclick();
    assert.match(status.textContent,/1件削除しました/);
    assert.deepEqual([...fixture.rows.keys()],[third]);
  } finally {
    delete global.window;
  }
});

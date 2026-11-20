const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const script = fs.readFileSync(path.join(__dirname, 'estimate_uploader_ja.js'), 'utf8');

function text(value) { return {nodeValue: value}; }
function dropzone(versions) {
  const label = text('Browse files');
  const attributes = {};
  const nodes = versions.map(text);
  const button = {nodes:[label], get textContent(){return label.nodeValue;},
                  setAttribute(name, value) { attributes[name]=value; },
                  onclick() {}, onkeydown() {}};
  const instructions = {nodes};
  return { querySelector(q) { return q === 'button' ? button : instructions; }, button, attributes, nodes };
}
function browser(initial) {
  let zone = initial, handler;
  const doc = {
    body: {},
    querySelectorAll(q) { assert.match(q,/^\.st-key-beginner_intake /); return [zone]; },
    createTreeWalker(element) { const nodes = [...element.nodes]; return {nextNode() {return nodes.shift();}}; },
  };
  class Observer {
    constructor(fn){ handler=fn; }
    observe(root, options){assert.equal(root,doc.body);assert.equal(options.childList,true);}
  }
  const globals = {document:doc,NodeFilter:{SHOW_TEXT:4},MutationObserver:Observer};
  vm.runInNewContext(script, globals);
  return {update(next){zone=next;handler();}, rerun(){vm.runInNewContext(script, globals);}};
}

test('translates visible text and accessible name of the same native button', () => {
  const zone = dropzone(['Drag and drop ', 'files', ' here', 'Limit 20MB per file', ' • PDF, JPG']);
  const click = zone.button.onclick, keyboard = zone.button.onkeydown;
  const page = browser(zone);
  assert.equal(zone.button.textContent,'ファイルを選ぶ');
  assert.equal(zone.attributes['aria-label'],'ファイルを選ぶ');
  assert.equal(zone.attributes.title,'ファイルを選ぶ');
  assert.equal(zone.nodes.map(node=>node.nodeValue).join(''),'ここにファイルをドロップ1ファイル 20MBまで • PDF, JPG');
  assert.equal(zone.button.onclick,click);
  assert.equal(zone.button.onkeydown,keyboard);
  page.rerun();
  assert.equal(zone.button.textContent,'ファイルを選ぶ');
});

test('new native widget after rerender is localized without changing allowed types', () => {
  const page = browser(dropzone([]));
  const newWidget = dropzone(['Drag and drop file here','Limit 20 MB per file • PDF, JPG']);
  page.update(newWidget);
  assert.equal(newWidget.button.textContent,'ファイルを選ぶ');
  assert.equal(newWidget.nodes[0].nodeValue,'ここにファイルをドロップ');
  assert.equal(newWidget.nodes[1].nodeValue,'1ファイル 20 MBまで • PDF, JPG');
});

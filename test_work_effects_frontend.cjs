const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/work_effects_frontend/effects.js','utf8');
function setup({unsupported=false}={}) {
  const items={}, messages=[], oscillators=[], gains=[], listeners={}, pending=new Map(); let serial=0, particles=0;
  for(const id of ['sound','sample','volume','volumeValue','motion','theme','stage','icon','message','hint','card'])items[id]={value:'',checked:true,textContent:'',className:'',attrs:{},classList:{add(){}},setAttribute(k,v){this.attrs[k]=v;},getBoundingClientRect(){return {height:248};},appendChild(){particles++;}};
  items.volume.value='20';items.theme.value='shuffle';
  const document={hidden:false,querySelector:id=>items[id.slice(1)],addEventListener:(event,fn)=>listeners[event]=fn,querySelectorAll:()=>[],createElement:()=>({style:{setProperty(){}},addEventListener(){}})};
  const window={parent:{postMessage:m=>messages.push(m)},addEventListener:(event,fn)=>listeners[event]=fn};
  window.AudioContext=class {constructor(){if(unsupported)throw Error();this.state='suspended';this.currentTime=0;this.destination={};}async resume(){this.state='running';}createGain(){const gain={gain:{cancelScheduledValues(){},setTargetAtTime(){},setValueAtTime:v=>gains.push(v),linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};return gain;}createOscillator(){const o={frequency:{},connect(){},disconnect(){},start(){oscillators.push(this.frequency.value);},stop(){}};return o;}};
  const engine=vm.runInNewContext(source+'\ncreateEffects(document);',{document,window,localStorage:{getItem:()=>null,setItem(){}},matchMedia:()=>({matches:false}),ResizeObserver:class{observe(){}},Math,Date,JSON,Set,Array,Number,String,Error,requestAnimationFrame:fn=>fn(),setTimeout:fn=>{pending.set(++serial,fn);return serial;},clearTimeout:id=>pending.delete(id)});
  const flush=()=>{for(const [id,fn] of [...pending]){pending.delete(id);fn();}};
  const event=(id,stage,age=0)=>engine.receive([{id,stage,at:Date.now()/1000-age}]);
  return {items,messages,oscillators,gains,flush,event,document,listeners,get particles(){return particles;}};
}
(async()=>{
  const t=setup();
  t.event('a','upload');t.flush();assert.equal(t.oscillators.length,0,'no autoplay before opt in');
  await t.items.sound.onclick();assert.equal(t.items.sound.attrs['aria-pressed'],'true');assert(t.oscillators.length>0);
  t.oscillators.length=0;t.items.theme.value='glass';
  t.event('b','read');t.flush();const first=[...t.oscillators];assert.equal(first.length,4);
  t.event('b','read');t.flush();assert.equal(t.oscillators.length,4,'rerun does not replay');
  t.event('c','read');t.flush();assert.notDeepEqual(t.oscillators.slice(4),first,'same motif does not repeat');
  t.oscillators.length=0;t.event('old','output',60);t.flush();assert.equal(t.oscillators.length,0);
  t.items.volume.value='0';t.event('silent','output');t.flush();assert.equal(t.oscillators.length,0);
  t.items.volume.value='50';t.document.hidden=true;t.event('hidden','finish');t.flush();assert.equal(t.oscillators.length,0);
  t.document.hidden=false;await t.items.sound.onclick();t.event('muted','check');t.flush();assert.equal(t.oscillators.length,0);
  t.items.motion.checked=false;const before=t.particles;await t.items.sample.onclick();assert.equal(t.particles,before);assert(t.oscillators.length>0);assert(Math.max(...t.gains)<=.14,'master volume capped');
  const unavailable=setup({unsupported:true});await unavailable.items.sound.onclick();assert.match(unavailable.items.hint.textContent,/消音のまま見積は作れます/);
  console.log('効果音：開始・再描画時の重複防止・毎回の変化・古い通知抑止・消音・音量上限・非表示・演出オフ・音声非対応を確認しました。');
})().catch(error=>{console.error(error);process.exitCode=1;});

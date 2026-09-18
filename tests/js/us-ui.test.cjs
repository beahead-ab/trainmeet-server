const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../../src/tmbox_gateway');

function fixture(role='dispatcher') {
 const statuses=['draft','transmitted','received','readback_pending','active','release_requested','closed','void'];
 return {role,conductors:[],clock:{time:'06:15:00'},session:{id:'session-1',name:'Spara',revision:8,status:'running',
  package:{territories:[{id:'territory',name:'Test Subdivision'}],nodes:[{id:'a',territory_id:'territory',name:'North Yard',mp:10,x:30,y:0},{id:'b',territory_id:'territory',name:'South Yard',mp:20,x:30,y:100}],segments:[{id:'main',name:'Main',from_node:'a',to_node:'b'}]},
  runs:[{id:'run-1',symbol:'SP 834',direction:'east',conductor_name:'Spara',schedule:[{node_id:'a',time:'06:00',work:'Switch'}],position:{segment_id:'main',mp:12,meet_time:'06:10',recorded_at:'2026-09-18T06:10:00Z'}}],
  warrants:statuses.map((status,i)=>({id:'w'+i,number:'W'+i,run_id:'run-1',kind:'proceed',status,text:'W'+i+' · SP 834\nProceed from MP 10 to MP 20\nSpara <unchanged>',path:[{segment_id:'main',from_mp:10,to_mp:20}],times:{[status]:{meet_time:'06:10'}}})),events:[]}};
}

// Execute the actual browser module with a minimal DOM surface. Rendering,
// translation and action dialogs run as shipped, with no network or timers.
async function setup(role='dispatcher') {
 const elements=new Map();
 function element(selector) {
  if(!elements.has(selector)) elements.set(selector,{innerHTML:'',textContent:'',dataset:{},
   querySelectorAll:()=>[],querySelector:(key)=>key===':focus'?null:element(selector+' '+key),
   contains:()=>false,setAttribute(){},showModal(){},close(){},classList:{toggle(){}}});
  return elements.get(selector);
 }
 const document={documentElement:{lang:'',dataset:{i18nScope:'us'}},body:element('body'),activeElement:null,
  querySelector:element,querySelectorAll:()=>[],addEventListener(){},
  createElement:()=>({set innerHTML(value){this.value=value.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"');}})};
 const data=fixture(role), requests=[];
 const context={document,navigator:{languages:['sv-SE']},location:{pathname:'/us/'+role,origin:'http://local.test'},
  localStorage:{getItem:key=>key==='trainmeet.language'?'sv':null,setItem(){}},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},
  addEventListener(){},setTimeout:()=>0,clearTimeout(){},AbortController,
  fetch:async(url,options)=>{requests.push({url,method:options.method});return {ok:true,json:async()=>data};}};
 context.window=context;
 vm.createContext(context);
 for(const file of ['web/i18n-messages.js','web/i18n.js']) vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context);
 const source=fs.readFileSync(path.join(root,'us_web/app.js'),'utf8');
 const api=await vm.runInContext('(async()=>{'+source+'\nreturn {state,runStatus,warrantCard,stripMap,action,holding};})()',context);
 return {api,language:context.TrainMeetI18n,requests,app:element('#app'),editor:element('#editor'),data};
}

test('populated US dispatcher renders in English, including map marks, limits and train pins',async()=>{
 const {app,language}=await setup();
 assert.equal(language.getLanguage(),'en');
 assert.match(app.innerHTML,/Train dispatcher · Track Warrant Control/);
 assert.match(app.innerHTML,/Track warrants/);assert.match(app.innerHTML,/>Eastbound ↓</);
 assert.match(app.innerHTML,/class="position-pin/);assert.match(app.innerHTML,/class="authority-band/);
 assert.match(app.innerHTML,/North Yard/);
});
test('unconfirmed warrants explicitly have no effect; lifecycle action keys are unchanged',async()=>{
 const {api}=await setup();
 for(const w of api.state.data.session.warrants.slice(0,4)) {
  const card=api.warrantCard(w);assert.match(card,/not in effect/);assert.match(card,/Not authority to move/);
 }
 assert.match(api.warrantCard(api.state.data.session.warrants[0]),/data-action="transmit"/);
 assert.match(api.warrantCard(api.state.data.session.warrants[3]),/data-action="activate"/);
});
test('conductor reports clear rather than claiming an already released authority',async()=>{
 const {api,app}=await setup('conductor');
 assert.match(app.innerHTML,/Your track warrants/);assert.match(app.innerHTML,/Report clear of limits/);
 assert.match(app.innerHTML,/data-action="request_release"/);
 const w=api.state.data.session.warrants[5];
 assert.equal(api.holding(w),true);
 assert.match(api.warrantCard(w),/Release reported · awaiting confirmation/);
 assert.match(api.warrantCard(w),/class="pill pending"/);
 assert.doesNotMatch(api.warrantCard(w),/class="pill active"/);
 assert.match(api.warrantCard(w),/Limits remain reserved/);
 assert.equal(api.runStatus(api.state.data.session.runs[0]),'Release reported · awaiting confirmation');
});
test('all five UI languages preserve issued text, domain data and command keys without writes',async()=>{
 const {api,language,requests,app,data}=await setup('conductor');
 const before=JSON.stringify(data),count=requests.length;
 for(const locale of ['sv','da','nb','de','en']) {
  language.setLanguage(locale);
  const card=api.warrantCard(data.session.warrants[4]);
  assert.match(card,/<pre>Proceed from MP 10 to MP 20\nSpara &lt;unchanged&gt;<\/pre>/);
  assert.match(card,/data-action="request_release"/);
  assert.match(app.innerHTML,/SP 834/);assert.match(app.innerHTML,/Spara/);
  assert.equal(JSON.stringify(data),before);assert.equal(requests.length,count);
 }
});
test('release dialog requires the entire train clear and preserves the full warrant',async()=>{
 const {api,editor,language,requests}=await setup('conductor');
 await api.action('request_release','w4');
 assert.match(editor.innerHTML,/My entire train is clear/);
 assert.match(editor.innerHTML,/will not use this authority again/);
 assert.match(editor.innerHTML,/<input name="confirmed" type="checkbox" required>/);
 assert.match(editor.innerHTML,/<pre class="preview-text">W4 · SP 834\nProceed from MP 10 to MP 20\nSpara &lt;unchanged&gt;<\/pre>/);
 language.setLanguage('de');
 assert.match(editor.innerHTML,/Proceed from MP 10 to MP 20/);
 assert.equal(requests.some(r=>r.method==='POST'),false);
});
test('new US terminology has complete translations without changing direction or warrant enums',async()=>{
 const {language}=await setup();
 const terms=['Train dispatcher','Eastbound','Westbound','Report clear of limits','In effect','Your track warrants','Proceed from … to …','Work between … and …'];
 for(const locale of ['sv','da','nb','en','de']) {
  language.setLanguage(locale);
  for(const source of terms) language.t(source);
 }
 const missing=language.missing();
 for(const source of terms) assert.equal(missing.includes(source),false,source);
 const source=fs.readFileSync(path.join(root,'us_web/app.js'),'utf8');
 assert.match(source,/<option value="east">Eastbound<\/option>/);
 assert.match(source,/<option value="work">Work between … and …<\/option>/);
 assert.doesNotMatch(source,/returnhtml`/);
});

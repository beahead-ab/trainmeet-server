const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../../src/tmbox_gateway/web');
function setup({stored=null,languages=['sv-SE'],blocked=false,scope='',usStored=null}={}) {
 const storage=new Map([['trainmeet.language',stored],['trainmeet.language.us',usStored],['tkl-language',stored]]),listeners={};
 const document={documentElement:{lang:'',dataset:{i18nScope:scope}},querySelectorAll:()=>[],createElement:()=>({set innerHTML(value){this.value=value.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"');}})};
 const context={document,navigator:{languages},localStorage:{getItem(key){if(blocked)throw Error('blocked');return storage.get(key);},setItem(key,value){if(blocked)throw Error('blocked');storage.set(key,value);}},addEventListener:(key,fn)=>listeners[key]=fn};
 vm.createContext(context);
 for(const name of ['i18n-messages.js','i18n.js'])vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context);
 return {api:context.TrainMeetI18n,context,storage,listeners};
}
test('five locales, OS normalization and persisted preference',()=>{
 const {api,context,storage}=setup({stored:'de-DE',languages:['sv-SE']});
 assert.equal(api.getLanguage(),'de');assert.equal(context.document.documentElement.lang,'de');
 assert.equal(api.t('Spara'),'Speichern');
 api.setLanguage('no-NO');assert.equal(api.getLanguage(),'nb');assert.equal(storage.get('trainmeet.language'),'nb');
 assert.equal(api.t('Användarnamn'),'Brukernavn');assert.equal(api.normalize('nn'),'nb');assert.equal(api.normalize('fr'),null);
});
test('unsupported browser languages fall through, private browsing works',()=>{
 const {api}=setup({languages:['fr-FR','da-DK'],blocked:true});assert.equal(api.getLanguage(),'da');
 api.setLanguage('en');assert.equal(api.t('Språk'),'Language');
});
test('storage events synchronize tabs without any network or reload',()=>{
 const {api,listeners}=setup();let count=0;const unsub=api.subscribe(()=>count++);
 listeners.storage({key:'trainmeet.language',newValue:'en'});assert.equal(api.getLanguage(),'en');assert.equal(count,1);
 unsub();api.setLanguage('de');assert.equal(count,1);
});
test('US defaults to American English regardless of browser, EU or old TKL preference',()=>{
 for(const blocked of [false,true]) {
  const {api,context,storage}=setup({scope:'us',stored:'sv',languages:['da-DK'],blocked});
  assert.equal(api.getLanguage(),'en');assert.equal(api.getLocale(),'en-US');
  assert.equal(context.document.documentElement.lang,'en');
  assert.equal(storage.get('trainmeet.language'),'sv');
 }
 assert.equal(setup({scope:'us',usStored:'fr',stored:'de'}).api.getLanguage(),'en');
});
test('US explicit choice persists separately and only US tabs synchronize it',()=>{
 const {api,storage,listeners}=setup({scope:'us',stored:'sv',usStored:'de'});
 assert.equal(api.getLanguage(),'de');
 api.setLanguage('nb');assert.equal(storage.get('trainmeet.language.us'),'nb');
 assert.equal(storage.get('trainmeet.language'),'sv');
 listeners.storage({key:'trainmeet.language',newValue:'da'});assert.equal(api.getLanguage(),'nb');
 listeners.storage({key:'trainmeet.language.us',newValue:'en'});assert.equal(api.getLanguage(),'en');
 const eu=setup({stored:'sv',usStored:'en'});
 eu.listeners.storage({key:'trainmeet.language.us',newValue:'de'});assert.equal(eu.api.getLanguage(),'sv');
 eu.api.setLanguage('en');assert.equal(eu.api.getLocale(),'en-GB');
});
test('both US operating routes use the explicitly scoped English document',()=>{
 const source=fs.readFileSync(path.join(root,'../us_web/index.html'),'utf8');
 assert.match(source,/<html lang="en" data-i18n-scope="us">/);
 assert.match(source,/href="\/us\/dispatcher"/);assert.match(source,/href="\/us\/conductor"/);
});
test('unknown source is preserved and parameters never treated as replacement syntax',()=>{
 const {api,context}=setup();assert.equal(api.t('Cda 8266'),'Cda 8266');
 context.TrainMeetMessages['test {name}']={sv:'Hej {name}'};
 assert.equal(api.t('test {name}',{name:'$& Cda'}),'Hej $& Cda');
});
test('UI markup translates authored text only, never train/station values or action keys',()=>{
 const {api}=setup({stored:'de'});
 const userValue='Spara';
 const result=api.html`<button data-action="ready" ${'disabled'}>Spara</button><b>${userValue}</b><pre>Save</pre>`;
 assert.match(result,/data-action="ready" disabled/);
 assert.match(result,/>Speichern<\/tm-text>/);
 assert.match(result,/<b>Spara<\/b>/);
 assert.match(result,/<pre>Save<\/pre>/);
 assert.equal(result.includes('undefined'),false);
});
test('escaped interpolated HTML stays escaped',()=>{
 const {api}=setup({stored:'en'});
 const result=api.html`<p>${'&lt;img src=x onerror=alert(1)&gt;'}</p>`;
 assert.equal(result,'<p>&lt;img src=x onerror=alert(1)&gt;</p>');
});
test('option labels translate without changing their implicit wire values',()=>{
 const {api}=setup({stored:'de'});
 const result=api.html`<select><option>Save</option><option value="work">Work between / and</option></select>`;
 assert.match(result,/<option value="Save" data-tm-text="Save">Speichern<\/option>/);
 assert.match(result,/value="work"/);
 assert.match(result,/Arbeiten zwischen/);
});
test('reviewed UI terms have complete five-language rows and consistent parameters',()=>{
 const {context}=setup();
 const keys=['Språk','Spara','Logga in','Server name','Not authority to move.','Report authority released'];
 for(const key of keys)for(const locale of ['sv','da','nb','en','de'])assert.ok(context.TrainMeetMessages[key][locale],key+' '+locale);
 for(const [key,row]of Object.entries(context.TrainMeetMessages))for(const text of Object.values(row))assert.equal(typeof text,'string',key);
 for(const key of ['Ta {station} i tjänst','Hittade {name} med {count} stationer.','Träffklocka, hastighet {speed}×','TA {station} I TJÄNST']) {
  const expected=[...key.matchAll(/\{(\w+)\}/g)].map(match=>match[1]).sort();
  for(const text of Object.values(context.TrainMeetMessages[key]))assert.deepEqual([...text.matchAll(/\{(\w+)\}/g)].map(match=>match[1]).sort(),expected);
 }
});
test('independent Cloud and TKL packages vendor the identical runtime/catalog',()=>{
 for(const project of ['trainmeet-cloud','trainmeet-tkl'])for(const [source,target]of [['i18n.js','core.js'],['i18n-messages.js','messages.js']]) {
  const file=path.resolve(root,'../../../../',project,'src/i18n',target);
  if(fs.existsSync(file))assert.equal(fs.readFileSync(file,'utf8'),fs.readFileSync(path.join(root,source),'utf8'));
 }
});
test('reset confirmation remains the fixed API value in every UI language',()=>{
 const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
 assert.match(app,/JSON\.stringify\(\{ confirmation: "NOLLSTÄLL" \}\)/);
 assert.doesNotMatch(app,/confirmation:\s*t\(/);
});

const { t, html } = globalThis.TrainMeetI18n;
const app = document.querySelector('#app');
const editor = document.querySelector('#editor');
const conductorView = location.pathname.endsWith('/conductor');
const readStored = (key) => { try { return sessionStorage.getItem(key); } catch { return null; } };
const store = (key, value) => { try { value ? sessionStorage.setItem(key, value) : sessionStorage.removeItem(key); } catch { /* in-memory state still works */ } };
const state = { data: null, serverContext: null, contextBlocked: false, selected: '', online: false, busy: false, history: false, loginVisible: false, token: conductorView ? readStored('us-token') : null, pending: readStored(conductorView ? 'us-pending-conductor' : 'us-pending-dispatcher'), notice: '', signature: '' };
const pendingKey = conductorView ? 'us-pending-conductor' : 'us-pending-dispatcher';
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {draft:'Draft · not in effect',transmitted:'Transmitted · not in effect',received:'Received · not in effect',readback_pending:'Readback reported · not in effect',active:'In effect',release_requested:'Release reported · awaiting confirmation',closed:'Released',void:'Voided'};
const holding = (w) => ['active','release_requested'].includes(w.status);
const closed = (w) => ['closed','void'].includes(w.status);
const session = () => state.data?.session;
const run = (id) => session()?.runs.find((r) => r.id === id);
const disabled = (action='') => ['import','cloud','packages'].includes(action) || state.contextBlocked || !state.online || state.busy || Boolean(state.pending) || (session()?.status === 'closed' && !['create_session','review-package','config'].includes(action));
const button = (label, action, extra='', primary=false) => html`<button type="button" data-action="${action}" ${extra} ${!['details','history'].includes(action)&&disabled(action)?'disabled':''} class="${primary?'primary':''}">${escape(t(label))}</button>`;
// randomUUID is unavailable on ordinary LAN HTTP; getRandomValues still works.
function createCommandID() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return crypto.randomUUID();
  if (typeof globalThis.crypto?.getRandomValues !== 'function') throw new Error(t('Secure random numbers are unavailable. Use a current browser.'));
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return 'us-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
const workspace = conductorView ? 'conductor' : 'dispatcher';
store('trainmeet.workspace', workspace);
document.querySelector('#workspace-home').href = `/us/${workspace}`;
document.querySelector('#workspace-logout').onclick = async () => {
  try {
    await api('/v1/auth/logout', {});
    state.token=null; store('us-token',null); store('trainmeet.workspace',null);
    state.data=null; state.online=false; editor.close();
    location.href='/';
  } catch(error) { notify(error.message); }
};

async function api(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path === '/v1/us/cloud/download' ? 45000 : 8000);
  try {
    const response = await fetch(path, {method:body?'POST':'GET', credentials:'same-origin', cache:'no-store', signal:controller.signal,
      headers:{...(body?{'Content-Type':'application/json'}:{}), ...(state.token?{Authorization:`Bearer ${state.token}`}:{})}, ...(body?{body:JSON.stringify(body)}:{})});
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.message || t("Server rejected the request")); error.status = response.status; throw error; }
    return result;
  } finally { clearTimeout(timeout); }
}

function notify(message) { state.notice = message; status(); }
function status() {
  document.body.classList.toggle('disconnected', !state.online);
  document.querySelector('#connection').textContent = state.online ? t("● Server connected") : t("● Offline · stale state");
  document.querySelector('#clock').textContent = state.data?.clock?.time?.slice(0,5) || '--:--';
  document.querySelector('#workspace-label').textContent = t(conductorView ? 'Conductor' : 'Dispatcher');
  document.querySelector('#selected-meet').textContent = state.serverContext?.selected_meet?.name || '';
  document.querySelectorAll('[data-admin-navigation]').forEach((link) => { link.hidden = state.data?.role !== 'dispatcher'; });
  const notice = document.querySelector('#notice');
  const message = state.pending ? t("Command result is unconfirmed. No further operational actions are allowed until it is checked.") : !state.online && state.data ? t("Connection lost. Showing the last confirmed server state. Actions are blocked.") : t(state.notice);
  notice.hidden = !message;
  notice.innerHTML = html`${escape(message)}${state.pending?html`<button type="button" data-action="check-result">Check result</button>`:''}`;
  document.querySelectorAll('[data-action]:not([data-action="check-result"]):not([data-action="history"]):not([data-action="details"])').forEach((el) => { el.disabled = disabled(el.dataset.action); });
  editor.querySelectorAll('button[type="submit"]').forEach((el) => { el.disabled = editor.dataset.busy === 'true' || disabled(el.form?.dataset.usAction); });
}

async function refresh(force=false) {
  try {
    const context = await api('/v1/server-context');
    state.serverContext=context;
    if(context.operating_region!=='us' || !context.selected_meet?.publication_id) {
      blockContext(context.selected_meet ? 'This workspace is not available for the selected meet.' : 'Select a published meet in Server settings first.');
      return;
    }
    let next = await api('/v1/us/context');
    // A saved US session is not evidence that this server still represents it.
    // Never display or command a previous meet after an administrator switches.
    if(next.session && next.session.status!=='closed' && next.session.package?.publication_id!==context.selected_meet.publication_id) {
      blockContext('The selected config is changing. Waiting for the server to confirm the active session.');
      return;
    }
    if(next.session?.status==='closed' && next.session.package?.publication_id!==context.selected_meet.publication_id) {
      next={...next,session:null,clock:null};
    }
    const wasBlocked=state.contextBlocked;
    state.contextBlocked=false; state.data = next; state.online = true;
    const signature = JSON.stringify([context.selected_meet,next.session?.id,next.session?.revision,next.conductors,next.role,next.packages]);
    if(wasBlocked)state.signature='';
    if (force || signature !== state.signature) { state.signature = signature; render(); }
  } catch (error) {
    state.online = false;
    if (error.status === 401) {
      state.data = null; state.signature = ''; editor.close();
      if (conductorView) { state.token = null; store('us-token', null); }
    }
    if (error.status === 401 || !state.data) login(error.status === 401 ? '' : error.message);
  }
  status();
}

function blockContext(message) {
  state.contextBlocked=true; state.online=true; state.data=null; state.signature=''; state.loginVisible=false;
  editor.close();
  app.innerHTML=html`<section class="welcome"><h1>Workspace unavailable</h1><p>${escape(t(message))}</p><a class="button" href="/#workspaces">Change workspace</a></section>`;
  status();
}

async function command(action, payload={}, revision=session()?.revision) {
  if (disabled(action)) throw new Error(t("Wait for a confirmed connection and command result"));
  const id = createCommandID();
  state.pending = id; store(pendingKey, id); state.busy = true; status();
  try {
    const result = await api('/v1/us/commands', {action, command_id:id, session_id:session()?.id, expected_revision:revision, ...payload});
    state.pending = null; store(pendingKey, null);
    state.notice = 'Saved and confirmed by the server.';
    if (action === 'extra') state.selected = result.target_id;
    await refresh(true);
    return result;
  } catch (error) {
    if (error.status && error.status < 500) { state.pending=null; store(pendingKey,null); }
    else state.online=false;
    notify(`${error.message}${error.status===409?' ' + t('Close this dialog, review the updated state and try again.'):''}`);
    await refresh(true);
    throw error;
  } finally { state.busy=false; status(); }
}

async function checkResult() {
  if (!state.pending) return;
  try {
    const {result} = await api(`/v1/us/command-status?command_id=${encodeURIComponent(state.pending)}`);
    if (result) { state.pending=null; store(pendingKey,null); notify(t('Server confirmed revision {revision}. No command was resent.', {revision:result.revision})); }
    else notify('No committed result found yet. Check again; the command will not be resent automatically.');
    await refresh(true);
    if (!result) document.querySelector('#notice').append(' ' + t('No committed result yet; check again.'));
  } catch (error) { notify(error.message); }
}

function login(error='', force=false) {
  // Polling must never replace the form while someone is typing credentials.
  if (state.loginVisible && !force) {
    if (error) app.querySelector('.form-error').textContent = error;
    return;
  }
  state.loginVisible = true;
  app.innerHTML = html`<section class="login"><p class="eyebrow">Train Meet US · ${conductorView?t("Conductor"):t("Train dispatcher")}</p><h1>${conductorView?t("Join your local session"):t("Sign in to dispatch")}</h1><p>${conductorView?t("Ask the dispatcher for a one-time connection code. The dispatcher assigns your train after you connect."):t("Use your existing TrainMeet Server administrator account. No Cloud connection is needed.")}</p><form id="access-form">${conductorView?html`<label>Your name<input name="name" autocomplete="name" required maxlength="80"></label><label>Connection code<input name="code" inputmode="numeric" autocomplete="one-time-code" required placeholder="123 456"></label>`:html`<label>Username<input name="username" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label>`}<p class="form-error" role="alert">${escape(error)}</p><button class="primary" type="submit">${conductorView?t("Connect"):t("Sign in")}</button></form><p class="form-note">First installation? <a href="/">Open Server setup</a>.</p></section>`;
  app.querySelector('form').onsubmit = async (event) => {
    event.preventDefault(); const form=event.currentTarget, fields=new FormData(form), submit=form.querySelector('button'); submit.disabled=true;
    try {
      if (conductorView) { const paired=await api('/v1/pair', {device_kind:'us_conductor',display_name:fields.get('name'),pairing_code:fields.get('code')}); state.token=paired.access_token; store('us-token',state.token); }
      else await api('/v1/auth/login',{username:fields.get('username'),password:fields.get('password')});
      await refresh(true);
    } catch (err) { form.querySelector('.form-error').textContent=err.message; } finally { submit.disabled=false; }
  };
}

let editorOrigin, editorBaseline='';
const editorValues = () => JSON.stringify([...editor.querySelectorAll('input,select,textarea')].map(el=>[el.name,el.value,el.checked]));
function cancelEditor() {
  if(editor.dataset.busy==='true')return;
  if(editorValues()!==editorBaseline && !window.confirm(t('Close without saving changes?')))return;
  editor.close();
}
editor.addEventListener('cancel',event=>{event.preventDefault();cancelEditor();});
editor.addEventListener('close',()=>{
  const replacement=editorOrigin?.dataset.action && [...app.querySelectorAll('[data-action]')].find(el=>el.dataset.action===editorOrigin.dataset.action && el.dataset.warrant===editorOrigin.dataset.warrant);
  (editorOrigin?.isConnected?editorOrigin:replacement||document.querySelector('#workspace-home'))?.focus({preventScroll:true});
  editorOrigin=null;
});
function modal(title, content) {
  editorOrigin=document.activeElement;
  editor.dataset.busy='false';
  editor.innerHTML=html`<div class="dialog-head"><h2 id="dialog-title">${escape(title)}</h2><button type="button" data-close aria-label="${escape(t('Close'))}">×</button></div>${content}`;
  if(!editor.querySelector('.dialog-actions'))editor.insertAdjacentHTML('beforeend',html`<div class="dialog-actions"><button type="button" data-close>${escape(t('Close'))}</button></div>`);
  editor.querySelectorAll('[data-close]').forEach(button=>{button.onclick=cancelEditor;});
  editorBaseline=editorValues();
  editor.showModal();
  (editor.querySelector('input:not([type="hidden"]),select,textarea')||editor.querySelector('.dialog-actions [data-close]'))?.focus({preventScroll:true});
}

function bindForm(action, makePayload) {
  const revision=session()?.revision;
  const form=editor.querySelector('form');
  form.dataset.usAction = action;
  // Path defaults are populated after modal() and before bindForm().
  editorBaseline=editorValues();
  form.onsubmit=async(event)=>{event.preventDefault(); const error=form.querySelector('.form-error'); error.textContent='';
    if(editor.dataset.busy==='true' || disabled(action))return;
    const fields=new FormData(form);
    const controls=[...editor.querySelectorAll('button,input,select,textarea')].map(el=>[el,el.disabled]);
    editor.dataset.busy='true';editor.setAttribute('aria-busy','true');controls.forEach(([el])=>{el.disabled=true;});
    try { const payload=await makePayload(fields,form); await command(action,payload,revision); editor.close(); }
    catch(err){error.textContent=err.message;}
    finally{controls.forEach(([el,wasDisabled])=>{el.disabled=wasDisabled;});editor.dataset.busy='false';editor.removeAttribute('aria-busy');status();}
  };
  status();
}
const formEnd = (label) => html`<p class="form-error" role="alert"></p><div class="dialog-actions"><button type="button" data-close>${escape(t('Cancel'))}</button><button class="primary" type="submit">${escape(t(label))}</button></div>`;

function render() {
  if(state.contextBlocked || !state.data)return;
  if (conductorView && state.data.role !== 'conductor') { login(); return; }
  state.loginVisible = false;
  const current=session();
  if (!current || current.status === 'closed') {
    app.innerHTML=html`<section class="welcome"><p class="eyebrow">${current?t("Session complete"):t("Local-first operations")}</p><h1>${current?t("Session safely closed"):t("Start a US session")}</h1><p>${state.data.role==='dispatcher'?t("The server runs the meet selected in Cloud settings. Review its published config before starting."):t("Waiting for the dispatcher to start a session and assign your train.")}</p>${state.data.role==='dispatcher'?packageShelf():''}${current?html`<p class="form-note">${escape(current.name)} · final revision ${current.revision} · history retained on the server</p>`:''}</section>`;status();return;
  }
  if (!run(state.selected)) state.selected=current.runs[0]?.id || '';
  const scrolls=[...app.querySelectorAll('[data-scroll]')].map((el)=>[el.dataset.scroll,el.scrollTop,el.scrollLeft]);
  const focused=app.querySelector(':focus')?.dataset.run;
  if (state.data.role==='conductor') renderConductor(); else renderDispatcher();
  for (const [key,top,left] of scrolls) { const el=app.querySelector(`[data-scroll="${key}"]`); if(el){el.scrollTop=top;el.scrollLeft=left;} }
  if (focused) [...app.querySelectorAll('[data-run]')].find((el)=>el.dataset.run===focused)?.focus({preventScroll:true});
  status();
}

function runStatus(r) {
  const warrants=session().warrants.filter((w)=>w.run_id===r.id);
  if(warrants.some((w)=>w.status==='release_requested'))return t("Release reported · awaiting confirmation");
  if(warrants.some((w)=>w.status==='active'))return t("Authority in effect");
  if(warrants.some((w)=>!closed(w)))return t("Awaiting authority");
  return r.ready?t("Ready to copy"):t("Not started");
}
function positionLabel(r) {
  if(!r.position)return t("No position reported");
  const seg=session().package.segments.find((s)=>s.id===r.position.segment_id);
  return `${seg?.name || t("Track")} · MP ${r.position.mp} · ${r.position.meet_time}`;
}
function trainRow(r) {
  const warrants=session().warrants.filter((w)=>w.run_id===r.id&&!closed(w));
  return html`<button type="button" class="train-row ${state.selected===r.id?'selected':''}" data-run="${escape(r.id)}" aria-pressed="${state.selected===r.id}"><span class="row-top"><strong>${escape(trainLabel(r))}</strong><span class="pill">${escape(t(r.direction==='east'?'Eastbound':'Westbound'))} ${r.direction==='east'?'↓':'↑'}</span></span><span>${runStatus(r)}</span><span class="sub">${r.schedule[0]?escape(t('Scheduled {time}', {time:r.schedule[0].time})):t("Extra train")} · ${escape(warrants.map((w)=>w.number).join(', ')||t("No warrants"))}</span><span class="sub">${escape(r.conductor_name || t("Conductor not assigned"))}</span><span class="sub">${escape(positionLabel(r))}</span></button>`;
}

function warrantCard(w) {
  const r=run(w.run_id), selected=state.selected===w.run_id;
  const dispatcher=state.data.role==='dispatcher';
  const actionMap=dispatcher?{draft:['transmit','Transmit track warrant'],readback_pending:['activate','Verify readback & activate'],release_requested:['close_warrant','Confirm release']}:{transmitted:['receive','Acknowledge receipt'],received:['readback','Report readback'],active:['request_release','Report clear of limits']};
  const action=actionMap[w.status];
  return html`<article class="warrant-card ${selected?'selected':''}"><button class="warrant-select" type="button" data-run="${escape(w.run_id)}" aria-pressed="${selected}"><span class="row-top"><strong>${escape(w.number)} · ${escape(trainLabel(r))}</strong><span class="pill ${w.status==='active'?'active':!closed(w)?'pending':''}">${escape(t(labels[w.status]))}</span></span><pre>${escape(w.text.split('\n').slice(1).join('\n'))}</pre></button><small>${escape(Object.values(w.times).at(-1).meet_time)} · ${escape(w.kind==='work'?t("Work between limits"):t("Proceed"))}</small>${!holding(w)&&!closed(w)?html`<p class="state-help">Not authority to move.</p>`:''}${w.status==='release_requested'?html`<p class="state-help">Limits remain reserved until the dispatcher confirms release.</p>`:''}<div class="actions">${action?button(action[1],action[0],`data-warrant="${escape(w.id)}"`,true):''}${dispatcher&&['draft','transmitted','received','readback_pending'].includes(w.status)?button(t("Void"),'void',`data-warrant="${escape(w.id)}"`):''}</div></article>`;
}

function renderDispatcher() {
  const current=session(), selected=run(state.selected);
  const pending=current.warrants.filter((w)=>!holding(w)&&!closed(w));
  const active=current.warrants.filter(holding), history=current.warrants.filter(closed);
  app.innerHTML=html`<div class="page-heading"><div><p class="eyebrow">Train dispatcher · Track Warrant Control</p><h1>${escape(current.name)}</h1><p>Revision ${current.revision} · ${current.runs.length} train runs · ${active.length} track warrants reserving limits</p><p>${escape(t(state.data.clock?.running?'US clock running':'US clock paused'))} · ${escape(state.data.clock?.speed||1)}×</p></div><div class="toolbar">${button('US clock','clock')}${button('Selected meet config','config')}${button(t("Connect conductor"),'pair')}${button(t("+ Extra train"),'extra')}${button(t("Finish session"),'finish_session')}</div></div><div class="board"><section class="column" aria-label="All train runs"><div class="column-head"><h2>All trains</h2><small>${current.runs.length} runs</small></div><div class="scroll train-list" data-scroll="trains">${current.runs.map(trainRow).join('')||html`<p class="empty">No scheduled runs. Add an extra train.</p>`}</div>${selected?html`<div class="column-head">${button(t("Train details"),'details')}${button(t("Assign"),'assign')}</div>`:''}</section><section class="column" aria-label="Track diagram"><div class="column-head"><h2>The railroad</h2><small>Reported positions · mileposts</small></div><div class="scroll map-viewport" data-scroll="map">${stripMap()}</div><div class="map-key"><span><i></i>Reserved authority limits</span><span><i class="draft"></i>Proposed · not in effect</span></div></section><section class="column" aria-label="Track warrants"><div class="column-head"><h2>Track warrants</h2>${selected?button(t("+ Draft"),'draft','',true):''}</div><div class="scroll warrant-list" data-scroll="warrants"><p class="list-label">Needs attention · ${pending.length}</p>${pending.map(warrantCard).join('')||html`<p class="muted">Nothing waiting.</p>`}<p class="list-label">In effect / release pending · ${active.length}</p>${active.map(warrantCard).join('')||html`<p class="muted">No reserved authority limits.</p>`}<p class="list-label">History · ${history.length}</p>${history.length?html`<button type="button" data-action="history">${state.history?t("Hide"):t("Show")} history</button>`:''}${state.history?history.map(warrantCard).join(''):''}</div></section></div>`;
}

function packageShelf() {
  const selectedId=state.serverContext?.selected_meet?.publication_id;
  const packages=(state.data.packages||[]).filter((p)=>p.publication_id===selectedId);
  return html`<section class="package-shelf"><h2>Selected meet config</h2><p class="form-note">Config is maintained in Cloud. Downloaded config remains available without internet.</p>${packages.map((p)=>html`<article class="package-row"><div><strong>${escape(p.name)}</strong><p>${p.counts.runs} ${escape(t('train runs'))} · ${p.counts.segments} ${escape(t('track segments'))}</p><small>${escape(p.published_at||p.downloaded_at)} · ${escape(p.publication_id)}</small></div>${button('Review package','review-package',`data-package="${escape(p.publication_id)}"`)}</article>`).join('')||html`<p>The selected config is not ready yet. Check Cloud connection in Server settings.</p>`}</section>`;
}

function packagePreview(p) {
  const node=(id)=>p.nodes.find((n)=>n.id===id);
  return html`<details open><summary>The railroad</summary><div class="package-table"><table><thead><tr><th>Track segment</th><th>From MP</th><th>To MP</th></tr></thead><tbody>${p.segments.map((s)=>html`<tr><td>${escape(s.name)}</td><td>${escape(node(s.from_node)?.name)} · ${escape(node(s.from_node)?.mp)}</td><td>${escape(node(s.to_node)?.name)} · ${escape(node(s.to_node)?.mp)}</td></tr>`).join('')}</tbody></table></div></details><details><summary>Train schedule & job instructions</summary>${p.runs.map((r)=>html`<h3>${escape(trainLabel(r))} · ${escape(t(r.direction==='east'?'Eastbound':'Westbound'))}</h3>${schedule(r,p)}`).join('')}</details>`;
}

async function reviewPackage(id) {
  if(id!==state.serverContext?.selected_meet?.publication_id)return;
  const p=state.data.packages?.find((item)=>item.publication_id===id);
  if(!p)return;
  const {package: details}=await api(`/v1/us/package?publication_id=${encodeURIComponent(id)}`);
  if(state.contextBlocked || id!==state.serverContext?.selected_meet?.publication_id)return;
  const settings=p.session||{}, planning=p.planning||{}, canStart=!session()||session().status==='closed';
  modal(t('Selected meet config'),html`<h3>${escape(p.name)}</h3><p class="form-note">${escape(p.publication_id)}</p><p>${p.counts.territories} ${escape(t('territories'))} · ${p.counts.nodes} ${escape(t('named points'))} · ${p.counts.segments} ${escape(t('track segments'))} · ${p.counts.runs} ${escape(t('train runs'))}</p><p>The session clock starts paused. This server runs one selected meet.</p><p>${escape(settings.clock_time||'12:00')} · ${escape(settings.clock_speed||1)}× · ${escape(settings.timezone||'')}</p><details><summary>Source instructions</summary>${(planning.source_instructions||[]).map((n)=>html`<p>${escape(n.text)}</p>`).join('')}</details>${packagePreview(details)}${canStart?html`<form><label class="confirm-label"><input name="confirmed" type="checkbox" required>I reviewed the topology and the model-railroad test profile.</label>${formEnd(t('Start US session'))}</form>`:html`<p class="form-note">Read-only config. Changes are published in Cloud and applied safely by the server.</p>`}`);
  if(canStart)bindForm('create_session',async(fields)=>({publication_id:p.publication_id,package_checksum:p.checksum,confirmed:fields.get('confirmed')==='on'}));
}

function stripMap() {
  const current=session(), p=current.package;
  const nodes=new Map(p.nodes.map((n)=>[n.id,n])), segments=new Map(p.segments.map((s)=>[s.id,s]));
  const xy=(n)=>({x:n.x*6,y:n.y+65});
  const point=(id,mp)=>{const s=segments.get(id),a=nodes.get(s.from_node),b=nodes.get(s.to_node),t=(mp-a.mp)/(b.mp-a.mp),aa=xy(a),bb=xy(b);return{x:aa.x+(bb.x-aa.x)*t,y:aa.y+(bb.y-aa.y)*t};};
  const path=(s)=>{const a=xy(nodes.get(s.from_node)),b=xy(nodes.get(s.to_node));return`M${a.x},${a.y}L${b.x},${b.y}`;};
  const height=Math.max(...p.nodes.map((n)=>n.y))+160;
  const rails=p.segments.map((s)=>html`<path class="tie" d="${path(s)}"/><path class="rail" d="${path(s)}"/>`).join('');
  const marks=p.nodes.map((n,i)=>{const v=xy(n),first=p.nodes.findIndex((other)=>other.territory_id===n.territory_id&&other.y===n.y)===i;return html`<circle class="point" cx="${v.x}" cy="${v.y}" r="5"/>${first?html`<path class="limit-guide" d="M62,${v.y}H565"/><text class="mp-label" x="15" y="${v.y+5}">${escape(n.mp)}</text><text x="380" y="${v.y+5}">${escape(n.name)}</text>`:''}`;}).join('');
  const bands=current.warrants.filter((w)=>!closed(w)).flatMap((w)=>w.path.map((leg)=>{const a=point(leg.segment_id,leg.from_mp),b=point(leg.segment_id,leg.to_mp);return html`<path class="authority-band ${holding(w)?'':'proposed'} ${state.selected===w.run_id?'chosen':''}" d="M${a.x-12},${a.y}L${b.x-12},${b.y}"/>`;})).join('');
  const pins=current.runs.filter((r)=>r.position).map((r)=>{const v=point(r.position.segment_id,r.position.mp);return html`<g class="position-pin ${state.selected===r.id?'chosen':''}" tabindex="0" role="button" aria-label="Select ${escape(trainLabel(r))}, reported MP ${r.position.mp}" data-run="${escape(r.id)}"><circle class="position-dot" cx="${v.x}" cy="${v.y}" r="7"/><rect x="${v.x+14}" y="${v.y-15}" width="165" height="31" rx="9"/><text x="${v.x+24}" y="${v.y+5}">${escape(trainLabel(r,false))} ${r.direction==='east'?'↓':'↑'} · ${r.position.mp}</text><title>Reported ${escape(r.position.meet_time)} · ${escape(r.position.recorded_at)}</title></g>`;}).join('');
  return html`<svg class="strip-map" viewBox="0 0 600 ${height}" role="img" aria-label="Schematic topology, reported train positions and track warrant limits"><text class="mp-label" x="15" y="27">MP</text><text class="track-name" x="125" y="27">${escape(p.territories.map((t)=>t.name).join(' / '))}</text>${rails}${bands}${marks}${pins}</svg>`;
}

function renderConductor() {
  const current=session(), selected=run(state.selected);
  if(!selected){app.innerHTML=html`<section class="welcome"><h1>Connected</h1><p>Waiting for the dispatcher to assign your train. This page updates automatically.</p></section>`;return;}
  const warrants=current.warrants.filter((w)=>w.run_id===selected.id&&!closed(w));
  app.innerHTML=html`<div class="conductor-layout">${current.runs.length>1?html`<div>${current.runs.map(trainRow).join('')}</div>`:''}<section class="card"><p class="eyebrow">Conductor · assigned train</p><h1>${escape(trainLabel(selected))} <small>${escape(t(selected.direction==='east'?'Eastbound':'Westbound'))}</small></h1><p>${escape(current.name)}</p><p class="muted">${escape(selected.locomotive||t("Locomotive not specified"))} · ${escape(selected.conductor_name)}</p><p class="muted">${escape(positionLabel(selected))}</p><div class="actions">${button(selected.ready?t("Ready reported"):t("Ready to copy"),'ready')}${button(t("Report"),'report','',true)}${button(t("Request authority"),'request')}</div></section><section class="card"><h2>Your track warrants</h2>${warrants.map(warrantCard).join('')||html`<p class="muted">No authority issued. Timetable times do not authorize movement.</p>`}</section><section class="card"><h2>Train schedule & job instructions</h2>${schedule(selected)}</section><section class="card"><h2>Reports & confirmations</h2>${events(selected)}</section></div>`;
}
function trainLabel(r,includeService=true) {
  if(!r)return '';
  const railroad=r.railroad?.trim();
  const identity=railroad && !r.symbol.toLowerCase().startsWith(railroad.toLowerCase()+' ') ? `${railroad} ${r.symbol}` : r.symbol;
  return includeService && r.service?.trim() ? `${identity} · ${r.service.trim()}` : identity;
}
function schedule(r,p=session().package) {
  const eventLabels={arrive:'Arrival',depart:'Departure',pass:'Pass',switch:'Switching'};
  return r.schedule.map((s)=>html`<div class="schedule-row"><time>${escape(s.time)}</time><span>${escape(p.nodes.find((n)=>n.id===s.node_id)?.name)}<br><small>${escape(t(eventLabels[s.event]||''))}${s.event&&s.work?' · ':''}${escape(s.work||'')}</small></span></div>`).join('')||html`<p class="muted">Extra train · no planned stops.</p>`;
}
function events(r) { return session().events.filter((e)=>e.target_id===r.id||session().warrants.some((w)=>w.id===e.target_id&&w.run_id===r.id)).slice(0,20).map((e)=>html`<div class="event"><strong>${escape(e.meet_time)}</strong> · ${escape(e.action.replaceAll('_',' '))}<br><small>Revision ${e.revision} · ${escape(e.actor)} · ${escape(e.recorded_at)}</small></div>`).join('')||html`<p class="muted">No reports yet.</p>`; }

function segmentOptions() {return session().package.segments.map((s)=>html`<option value="${escape(s.id)}">${escape(s.name)} · ${escape(s.id)}</option>`).join('');}
function legForm() {return html`<div class="path-leg"><label>Track segment<select name="segment">${segmentOptions()}</select></label><div class="form-row"><label>From MP<input name="from" type="number" step="any" required></label><label>To MP<input name="to" type="number" step="any" required></label><button type="button" class="remove-leg" aria-label="Remove segment">×</button></div></div>`;}
function setupLeg(el) {
  const setLimits=()=>{const s=session().package.segments.find((v)=>v.id===el.querySelector('select').value),nodes=session().package.nodes;const a=nodes.find((n)=>n.id===s.from_node),b=nodes.find((n)=>n.id===s.to_node);el.querySelector('[name=from]').value=a.mp;el.querySelector('[name=to]').value=b.mp;};
  el.querySelector('select').onchange=setLimits;el.querySelector('.remove-leg').onclick=()=>el.remove();setLimits();
}

async function action(name, warrantId, packageId) {
  const selected=run(state.selected);
  if(name==='check-result'){await checkResult();return;}
  if(name==='history'){state.history=!state.history;render();return;}
  if(name==='details'&&selected){modal(trainLabel(selected),html`<p class="form-note">Session-specific run: ${escape(selected.id)}</p><h3>Train schedule & job instructions</h3>${schedule(selected)}<h3>Recent operations</h3>${events(selected)}`);return;}
  if(disabled(name))return;
  if(name==='config'){await reviewPackage(state.serverContext?.selected_meet?.publication_id);return;}
  if(name==='review-package'){await reviewPackage(packageId);return;}
  if(name==='clock'){
    const clock=state.data.clock||{};
    if(clock.source==='fastclock') {
      location.href='/#settings';
      return;
    }
    modal(t('US clock'),html`<form><p>Only this US session is affected. Clock time never grants movement authority.</p><label>Time<input name="time" type="time" step="1" required value="${escape(clock.time||'12:00:00')}"></label><label>Speed<input name="speed" type="number" min="0.1" max="60" step="any" required value="${escape(clock.speed||1)}"></label><label class="confirm-label"><input name="running" type="checkbox" ${clock.running?'checked':''}>Run US clock</label>${formEnd(t('Apply US clock'))}</form>`);
    bindForm('clock',async(fields)=>({clock_time:fields.get('time'),clock_speed:Number(fields.get('speed')),running:fields.get('running')==='on',confirmed:true}));return;
  }
  if(name==='pair'){
    const result=await api('/v1/us/conductor-code',{});modal(t("Connect a conductor"),html`<p>Open <strong>${escape(location.origin)}/us/conductor</strong> on the conductor’s device.</p><div class="pair-code">${escape(result.code)}</div><p class="form-note">One use · expires in ${result.expires_in_minutes} minutes. After connection, select a train and choose Assign.</p>`);return;
  }
  if(name==='assign'&&selected){
    modal(t('Assign {train}', {train:trainLabel(selected)}),html`<form><label>Connected conductor<select name="conductor" required><option value="">Choose a conductor</option>${(state.data.conductors||[]).map((c)=>html`<option value="${escape(c.id)}">${escape(c.name)}</option>`).join('')}</select></label><p class="form-note">Assignment is controlled by the dispatcher. Finish any outstanding transmitted/active warrants before changing conductor.</p>${formEnd(t("Assign train"))}</form>`);bindForm('assign',(fields)=>({run_id:selected.id,conductor_id:fields.get('conductor')}));return;
  }
  if(name==='extra'){
    modal(t("Add an extra train"),html`<form><label>Train symbol<input name="symbol" required maxlength="60" placeholder="Extra 401 East"></label><label>Direction<select name="direction"><option value="east">Eastbound</option><option value="west">Westbound</option></select></label><p class="form-note">A new run ID is created. The original timetable is unchanged.</p>${formEnd(t("Add train"))}</form>`);bindForm('extra',(fields)=>Object.fromEntries(fields));return;
  }
  if(name==='draft'&&selected){
    modal(t('Draft · {train}', {train:trainLabel(selected)}),html`<form><label>Authority type<select name="kind"><option value="proceed">Proceed from … to …</option><option value="work">Work between … and …</option></select></label><p class="form-note">Choose an ordered, connected track path. Limits may differ from the timetable. This draft gives no authority to move.</p><div id="legs">${legForm()}</div><button id="add-leg" type="button">+ Track segment</button><label>Additional information<textarea name="notes" maxlength="1000" placeholder="Not machine-validated. Do not hide track limits or conflict exceptions here."></textarea></label>${formEnd(t("Save draft for review"))}</form>`);
    setupLeg(editor.querySelector('.path-leg'));editor.querySelector('#add-leg').onclick=()=>{editor.querySelector('#legs').insertAdjacentHTML('beforeend',legForm());setupLeg(editor.querySelector('.path-leg:last-child'));};
    bindForm('draft',(fields,form)=>({run_id:selected.id,kind:fields.get('kind'),notes:fields.get('notes'),path:[...form.querySelectorAll('.path-leg')].map((el)=>({segment_id:el.querySelector('select').value,from_mp:Number(el.querySelector('[name=from]').value),to_mp:Number(el.querySelector('[name=to]').value)}))}));return;
  }
  if((name==='report'||name==='request')&&selected){
    modal(t('Report · {train}', {train:trainLabel(selected)}),html`<form><label>Report type<select name="kind"><option value="position">Position</option><option value="request" ${name==='request'?'selected':''}>Request new authority</option><option value="delay">Delay</option><option value="problem">Problem</option></select></label><div id="position-fields"><div class="form-row"><label>Track segment<select name="segment">${segmentOptions()}</select></label><label>Reported MP<input type="number" step="any" name="mp"></label></div></div><label>Report / uncertainty<textarea name="message" required maxlength="1000" placeholder="Front at MP… / stopped clear of… / estimate only…"></textarea></label><p class="form-note">Reports never release an authority. Use the warrant’s explicit release action.</p>${formEnd(t("Send report"))}</form>`);
    const kind=editor.querySelector('[name=kind]');const toggle=()=>{editor.querySelector('#position-fields').hidden=kind.value!=='position';editor.querySelector('[name=mp]').required=kind.value==='position';};kind.onchange=toggle;toggle();
    bindForm('report',(fields)=>({run_id:selected.id,kind:fields.get('kind'),message:fields.get('message'),...(fields.get('kind')==='position'?{position:{segment_id:fields.get('segment'),mp:Number(fields.get('mp'))}}:{})}));return;
  }
  if(name==='ready'&&selected){await command('ready',{run_id:selected.id});return;}
  const warrant=session().warrants.find((w)=>w.id===warrantId);
  const confirmations={transmit:['Transmit track warrant','The conductor must acknowledge receipt and read back this track warrant. Transmitting it does not put it in effect.'],receive:['Acknowledge receipt','I have received this exact track warrant. Receipt is not readback and does not authorize movement.'],readback:['Report readback','I have read back this exact warrant to the dispatcher by radio/telephone. I will wait for the dispatcher’s confirmation.'],activate:['Verify readback & activate','I verified the conductor’s readback against this exact warrant. The server will check conflicts atomically before activating.'],request_release:['Report clear of limits','My entire train is clear of the limits shown on this track warrant. I am reporting clear and will not use this authority again. In this test profile, the limits remain reserved until the dispatcher confirms release.'],close_warrant:['Confirm release','I verified the conductor’s report that the entire train is clear of these limits. Confirm release and close this track warrant.'],void:['Void track warrant','This is not an active authority. I have communicated that this warrant must not be used.'],finish_session:['Finish US session','Close this session. All warrants must already be closed or void; history is retained.']};
  const confirmation=confirmations[name];if(!confirmation)return;
  modal(t(confirmation[0]),html`<form>${warrant?html`<pre class="preview-text">${escape(warrant.text)}</pre>`:''}<label class="confirm-label"><input name="confirmed" type="checkbox" required>${escape(t(confirmation[1]))}</label>${formEnd(confirmation[0])}</form>`);bindForm(name,()=>({...(warrant?{warrant_id:warrant.id}:{}),confirmed:true}));
}

document.addEventListener('click', (event) => {
  const selection=event.target.closest('[data-run]');
  if(selection){state.selected=selection.dataset.run;render();return;}
  const target=event.target.closest('[data-action]');if(target&&!target.disabled)action(target.dataset.action,target.dataset.warrant,target.dataset.package).catch((error)=>notify(error.message));
});
document.addEventListener('keydown',(event)=>{const target=event.target.closest('g[data-run]');if(target&&['Enter',' '].includes(event.key)){event.preventDefault();state.selected=target.dataset.run;render();}});
window.addEventListener('offline',()=>{state.online=false;status();});
window.addEventListener('online',()=>{void refresh(true);});
// A language change is presentation-only: no fetch, command, form submit,
// session restart or loss of unsent input. An open authority dialog stays open.
TrainMeetI18n.subscribe(() => {
  const fields = [...app.querySelectorAll('input,select,textarea')].map((element) => ({name:element.name,value:element.value,checked:element.checked}));
  const focusedName = app.contains(document.activeElement) ? document.activeElement.name : null;
  if(state.contextBlocked) { status(); return; }
  if (state.data) render();
  else login('', true);
  for (const saved of fields) {
    const element = [...app.querySelectorAll('input,select,textarea')].find((field) => field.name === saved.name);
    if (element) { element.value = saved.value; element.checked = saved.checked; }
  }
  if (focusedName) [...app.querySelectorAll('input,select,textarea')].find((field) => field.name === focusedName)?.focus({preventScroll:true});
  status();
});
await refresh(true);
// Serial polling: no overlapping fetches and no queued operational writes.
async function poll(){await refresh();setTimeout(poll,2500);}setTimeout(poll,2500);

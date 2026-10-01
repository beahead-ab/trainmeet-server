// End-to-end browser smoke against REAL isolated HTTP servers + temporary SQLite.
// No page.route, response fixtures, production servers, MQTT or Cloud network IO.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'server-shell-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '';
  fixture.stderr.on('data', value => { stderr += value; });
  let browser;
  try {
    const urls = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', line => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', code => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [];
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1280, height: 960 } });
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => { errors.push(error.message); console.error('Browser:', error.message); });
    const requests = [];
    page.on('request', request => requests.push(request.url()));
    page.on('requestfailed', request => console.error('Request failed:', request.url(), request.failure()));
    const screenshot = async name => {
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, 'live-' + name + '.png'), fullPage: true });
    };
    async function login(base) {
      await page.goto(base + '/login');
      await page.locator('#login-form').waitFor({ state: 'visible' }).catch(async error => {
        console.error('Login state:', page.url(), await page.locator('body').innerText());
        console.error('Requests:', requests);
        console.error('Auth:', await (await page.request.get(base + '/v1/auth/status')).text());
        console.error('Setup:', await (await page.request.get(base + '/v1/setup')).text());
        await screenshot('login-failure');
        throw error;
      });
      await page.locator('#login-username').fill('smoke-admin');
      await page.locator('#login-password').fill('isolated-browser-test');
      await page.locator('#login-form button[type="submit"]').click();
      await page.locator('#overview-view').waitFor({ state: 'visible' });
    }

    await login(urls.eu);
    await page.locator('#device-list .status-row').waitFor();
    assert.equal(await page.locator('#overview-traffic').isVisible(),true);
    // Clients and the left/right setting are two cards: operations and a
    // setting used to share one, and the station table read as client rows.
    assert.equal((await page.locator('#device-management > .section-heading h2').textContent()).trim(),'Klienter');
    assert.equal(await page.locator('#device-management #server-station-rows').count(),0);
    assert.equal((await page.locator('#station-placement > h2').textContent()).trim(),'TMBox-placering');
    assert.equal(await page.locator('#station-placement #server-station-rows').count(),1);
    // The fixture box connected once and has not pinged since this server
    // started: no contact, with the time it was last seen, just before the
    // buttons. The pairing code is not shown here; boxes never use it.
    const smokeStatus=page.locator('#device-list .status-row').filter({hasText:'TBX-SMOKE'}).locator('.device-connection');
    assert.match(await smokeStatus.textContent(),/^Ingen kontakt · sist sedd \d{2}[:.]\d{2}$/);
    assert.equal(await smokeStatus.getAttribute('class'),'device-connection device-connection--lost');
    assert.ok(await smokeStatus.evaluate(status=>status.nextElementSibling.classList.contains('device-actions')),'Status sits just before the buttons');
    // Warning colour, not the station column's ink (app.css styled every span in the row as the station).
    const [statusColor,stationColor,statusWeight]=await smokeStatus.evaluate(status=>{const station=status.previousElementSibling;
      return [getComputedStyle(status).color,getComputedStyle(station).color,getComputedStyle(status).fontWeight];});
    assert.notEqual(statusColor,stationColor);
    assert.equal(statusWeight,'600');
    assert.doesNotMatch(await page.locator('#client-network').textContent(),/Kod/);
    assert.ok(await page.locator('#device-management').evaluate(card=>card.querySelector('#device-list').compareDocumentPosition(card.querySelector('.device-reconnect'))&Node.DOCUMENT_POSITION_FOLLOWING),'Reconnect comes after the client list');
    assert.equal(await page.locator('#overview-graph').isVisible(),true);
    assert.equal(await page.locator('#overview-view details').count(),0,'Drift folds nothing away');
    await page.locator('#overview-clock-start').click();
    await page.locator('#stop-local-clock').waitFor({state:'visible'});
    assert.equal((await (await page.request.get(urls.eu+'/v1/clock')).json()).running,true);
    await page.locator('#stop-local-clock').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    assert.equal((await (await page.request.get(urls.eu+'/v1/clock')).json()).running,false);
    for(const width of [1280,360]){
      await page.setViewportSize({width,height:900});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Drift overflow');
      await screenshot('drift-'+width);
    }
    const initialPlacement=await (await page.request.get(urls.eu+'/v1/cloud/presentation')).json();
    const launch=page.locator('#server-station-rows button').first();
    await launch.click();
    const placementForm=page.locator('.placement-inline-edit');
    await placementForm.locator('select').selectOption('right');
    await placementForm.locator('[type=submit]').click();
    await placementForm.waitFor({state:'hidden'});
    const saved=await (await page.request.get(urls.eu+'/v1/cloud/presentation')).json();
    assert.equal(saved.stations[0].connections[0].side,'right');
    const stale=await page.request.post(urls.eu+'/v1/cloud/display-placement',{data:{publication_id:initialPlacement.publication_id,config_version:initialPlacement.config_version,station_id:initialPlacement.stations[0].station_id,sides:{}}});
    assert.equal(stale.status(),409);
    await launch.click(); await placementForm.locator('select').selectOption('');
    await placementForm.getByRole('button',{name:'Avbryt',exact:true}).click();
    assert.equal((await (await page.request.get(urls.eu+'/v1/cloud/presentation')).json()).stations[0].connections[0].side,'right');
    for(const [width,station] of [[1280,'station-a'],[360,'station-b']]){
      await page.setViewportSize({width,height:900});
      const row=page.locator('#device-list .status-row').filter({hasText:'TBX-SMOKE'});
      await row.getByRole('button',{name:width===1280?'Tilldela station':'Ändra station',exact:true}).click();
      const form=row.locator('.device-inline-edit');
      await form.locator('select.device-station').selectOption(station);
      await form.locator('[type=submit]').click();
      await form.waitFor({state:'hidden'});
      assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices[0].station_id,station);
      await page.reload();
      await row.getByRole('button',{name:'Ändra station',exact:true}).click();
      assert.equal(await form.locator('select.device-station').inputValue(),station);
      await form.getByRole('button',{name:'Avbryt',exact:true}).click();
    }
    // Clock settings propagate to a separate unauthenticated TV context.
    await page.setViewportSize({width:1280,height:960});
    await page.locator('#local-clock-time').fill('14:26:00');
    await page.locator('#local-clock-speed').fill('4,3');
    await page.locator('#clock-control-form [type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#clock-control-form').dataset.dirty);
    await page.getByRole('link',{name:'Inställningar',exact:true}).click();
    await page.locator('#meet-clock-style').selectOption('digital');
    await page.locator('#meet-clock-seconds').check();
    await page.locator('#clock-appearance-form [type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#clock-appearance-form').dataset.dirty);
    const screenContext=await browser.newContext({viewport:{width:1920,height:1080}});
    const clockScreen=await screenContext.newPage();
    clockScreen.on('pageerror',e=>errors.push(e.message));
    await clockScreen.goto(urls.eu+'/display/clock?style=swedish');
    await clockScreen.locator('.clock-digital').filter({hasText:'14:26:00'}).waitFor();
    assert.equal(await clockScreen.locator('.sc-stopped').isVisible(),true);
    for(const style of ['analog','stationsur','swiss','digital']){
      await page.locator('#meet-clock-style').selectOption(style);
      await page.locator('#clock-appearance-form [type=submit]').click();
      await page.waitForFunction(()=>!document.querySelector('#clock-appearance-form').dataset.dirty);
      await clockScreen.waitForFunction(style=>document.querySelector('#clock-view').dataset.clockSignature.startsWith(style+'|'),style);
      assert.equal(await clockScreen.locator('.clock-face').count(),style==='digital'?0:1);
      assert.ok(await clockScreen.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));
    }
    for(const path of ['topology','graph','dashboard']){
      await clockScreen.goto(urls.eu+'/display/'+path);
      await clockScreen.locator('#display-loading').waitFor({state:'hidden'});
      // Översikt has no top row, as in the design: its first tile is the clock with the meet.
      assert.equal(await clockScreen.locator('#screen-header').isVisible(),path!=='dashboard');
      assert.equal(await clockScreen.locator('#screen-footer').isVisible(),true);
      if(path==='dashboard') assert.equal(await clockScreen.locator('.dashboard-clock-card .dashboard-clock-meta b').isVisible(),true);
      assert.equal(await clockScreen.locator('#screen-meet .sc-badge').count(),0,'Screens show the meet name, not EU/US');
      if(path==='topology'){
        const bounds=await clockScreen.locator('#topology-svg .topology-name').evaluateAll(nodes=>nodes.map(n=>Number(n.getAttribute('x'))));
        assert.ok(Math.max(...bounds)-Math.min(...bounds)>1000,'Two-station TV layout uses the available width');
      }
      if(path==='dashboard'){
        const cards=await clockScreen.locator('#dashboard-view > *').evaluateAll(nodes=>nodes.map(n=>({top:n.getBoundingClientRect().top,bottom:n.getBoundingClientRect().bottom})));
        assert.ok(cards[0].bottom<=cards[1].top,'Dashboard statistics do not overlap the map');
        // Stress the presentation with four future events. Render an isolated
        // snapshot only; no traffic or timetable records are changed.
        const snapshot=await (await page.request.get(urls.eu+'/v1/display')).json();
        const eventsFit=await clockScreen.evaluate(snapshot=>{
          renderDashboard({...snapshot,routes:Array.from({length:4},(_,i)=>({...snapshot.routes[0],departure_time:`23:5${i}`,arrival_time:null,train_number:String(900+i)}))});
          const card=document.querySelector('.server-dashboard-bottom .display-card');
          const events=[...card.querySelectorAll('.server-event')];
          return events.length===4 && events.every(row=>row.getBoundingClientRect().bottom<=card.getBoundingClientRect().bottom-8);
        },snapshot);
        assert.ok(eventsFit,'All four upcoming events fit inside the TV card');
      }
    }
    await screenContext.close();
    await page.goto(urls.eu+'/drift');
    await page.locator('#overview-clock-start').click();
    await page.locator('#stop-local-clock').waitFor({state:'visible'});
    await page.locator('#stop-local-clock').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    // Removing a client leaves actual traffic/history untouched.
    const before=await (await page.request.get(urls.eu+'/v1/display')).json();
    await page.locator('#device-list .device-remove').click();
    await page.locator('.device-inline-edit').getByRole('button',{name:'Avbryt',exact:true}).click();
    assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices.length,1);
    await page.locator('#device-list .device-remove').click();
    assert.match(await page.locator('.device-inline-edit').textContent(),/spärras och kommer inte tillbaka av sig själv/);
    await page.locator('.device-inline-edit').getByRole('button',{name:'Ta bort',exact:true}).click();
    await page.locator('#device-list .empty-status').waitFor();
    // Removed, but it was here a moment ago: it is offered back with one
    // click instead of copying its code from the box.
    const trying=page.locator('#device-removed-trying');
    await trying.getByText('Borttagna boxar som försöker ansluta').waitFor();
    await trying.locator('.status-row').filter({hasText:'TBX-SMOKE'}).getByRole('button',{name:'Återanslut',exact:true}).click();
    await trying.locator('select').selectOption('station-a');
    await trying.locator('[type=submit]').click();
    await page.locator('#device-list .status-row').filter({hasText:'TBX-SMOKE'}).waitFor();
    assert.equal(await trying.isHidden(),true);
    assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices[0].station_id,'station-a');
    // One box per side at a station: the side is chosen with the station, and shown unless it is both.
    const smokeRow=page.locator('#device-list .status-row').filter({hasText:'TBX-SMOKE'});
    await smokeRow.getByRole('button',{name:'Ändra station',exact:true}).click();
    const sideSelect=page.locator('#device-list .device-inline-edit select.device-side');
    assert.equal(await sideSelect.inputValue(),'both');
    assert.deepEqual(await sideSelect.locator('option').allTextContents(),['Båda sidor','Vänster','Höger']);
    await sideSelect.selectOption('left');
    await page.locator('#device-list .device-inline-edit [type=submit]').click();
    await page.locator('#device-list .status-row').filter({hasText:'· vänster'}).waitFor();
    assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices[0].station_side,'left');
    await smokeRow.getByRole('button',{name:'Ändra station',exact:true}).click();
    assert.equal(await sideSelect.inputValue(),'left','the form opens on the side the box has');
    await page.locator('#device-list .device-inline-edit').getByRole('button',{name:'Avbryt',exact:true}).click();
    await page.locator('#device-list .device-remove').click();
    await page.locator('.device-inline-edit').getByRole('button',{name:'Ta bort',exact:true}).click();
    await page.locator('#device-list .empty-status').waitFor();
    const after=await (await page.request.get(urls.eu+'/v1/display')).json();
    for(const key of ['stations','connections','routes','train_positions','connection_states'])assert.deepEqual(after[key],before[key]);
    await page.getByRole('link',{name:'Hjälp',exact:true}).click();
    await page.locator('#help-view').waitFor({state:'visible'});
    assert.equal(await page.locator('#help-view a[href="/tmbox-lab/"]').isVisible(),true);
    // Help leads to the flows the 16x2 engine draws; the ESP32/V1 panes it
    // used to fold in described boxes nobody runs any more.
    assert.equal(await page.locator('#help-view a[href="/tmbox-lab/floden"]').isVisible(),true);
    assert.equal(await page.locator('#help-view details').count(),0);
    assert.equal(await page.locator('#tmbox-flow-list > *').count(),0,'The V1/ESP32 flows are not built for Help');
    assert.equal(await page.locator('.screen-menu nav a[href="/tmbox-lab/floden"]').count(),1);
    // Legacy bookmarks resolve to canonical routes.
    await page.goto(urls.eu+'/#settings');await page.waitForURL(urls.eu+'/installningar');
    await page.goto(urls.eu+'/#overview');await page.waitForURL(urls.eu+'/drift');
    // Signed in, the guest page is out of reach: "/", the old picker address
    // and the logo all lead to Drift.
    for(const address of ['/','/#workspaces']){
      await page.goto(urls.eu+address);await page.waitForURL(urls.eu+'/drift');
      await page.locator('#overview-view').waitFor({state:'visible'});
      assert.equal(await page.locator('#participant-view').isVisible(),false);
    }
    await page.goto(urls.eu+'/installningar');
    await page.locator('#workspace-home').click();await page.waitForURL(urls.eu+'/drift');
    assert.equal(await page.locator('#participant-view').isVisible(),false);
    // Drop-down menus close on a click outside them and on Escape.
    const screenMenu=page.locator('details.screen-menu');
    await screenMenu.locator('summary').click();
    assert.equal(await screenMenu.getAttribute('open'),'');
    // The Open menu has the screens and, as its own group, both TMBox pages:
    // the test bench used to be reachable only from Help.
    assert.equal((await screenMenu.locator('summary').textContent()).trim(),'Öppna');
    assert.deepEqual(await screenMenu.locator('nav .tm-eyebrow:visible').allTextContents(),['Skärmar','TMBox']);
    for(const [href,text] of [['/tmbox/','Virtuell TMBox'],['/tmbox-lab/','Provbänk med testdata']]){
      const link=screenMenu.locator(`nav a[href="${href}"]`);
      assert.equal(await link.isVisible(),true,href);
      assert.equal((await link.textContent()).trim(),text);
      assert.equal(await link.getAttribute('target'),'_blank');
    }
    await page.mouse.click(4,600);
    assert.equal(await screenMenu.getAttribute('open'),null,'A click outside closes the menu');
    await screenMenu.locator('summary').click();
    await page.keyboard.press('Escape');
    assert.equal(await screenMenu.getAttribute('open'),null,'Escape closes the menu');
    // ⚙ › Skärmar och klocka is three parts that each save only their own
    // fields; the code for apps and TKL has its own category, Anslutning
    // (1.22.0). Farozon is the last card on the page.
    await page.goto(urls.eu+'/installningar');
    assert.deepEqual(await page.locator('.clock-control-card .server-part__title').allTextContents(),['Klocka','QR-koder på skärmarna','Träffens Wi-Fi']);
    assert.equal(await page.locator('#connection-settings #connection-code-form').count(),1);
    assert.equal(await page.locator('#admin-view > .server-card').last().getAttribute('data-anchor'),'farozon');
    await page.locator('#connection-wifi-name').fill('Test-Wifi');
    await page.locator('#connection-wifi-password').fill('test-only-1234');
    await page.locator('#connection-wifi-form [type=submit]').click();
    await page.locator('#connection-wifi-message').getByText('Sparat.').waitFor();
    await page.locator('#connection-badge-screens input[value="graph"]').uncheck();
    await page.locator('#connection-badge-form [type=submit]').click();
    await page.locator('#connection-badge-message').getByText(/Sparat/).waitFor();
    const connection=await (await page.request.get(urls.eu+'/v1/display/connection')).json();
    assert.equal(connection.wifi.name,'Test-Wifi','Saving the QR part leaves the Wi-Fi as it was');
    assert.ok(!connection.screens.includes('graph'));
    assert.equal(await page.locator('text=Serverns nätverk').count(),0);
    await page.locator('#web-client-ttl').fill('45');
    await page.locator('#connection-code-form [type=submit]').click();
    await page.locator('#connection-code-message').getByText(/Sparat/).waitFor();
    const afterCode=await (await page.request.get(urls.eu+'/v1/display/connection')).json();
    assert.equal(afterCode.web_client_ttl_minutes,45);
    assert.equal(afterCode.wifi.name,'Test-Wifi');
    assert.ok(!afterCode.screens.includes('graph'),'Saving the code part leaves the screens as they were');
    await login(urls.us);
    assert.equal(await page.locator('#workspace-options').count(),0);
    await page.locator('#server-region').filter({hasText:'US'}).waitFor();
    assert.equal(await page.locator('#drift-simulation').isVisible(),false);
    assert.equal(await page.locator('#device-management').isVisible(),false);
    assert.equal(await page.locator('#station-placement').isVisible(),false);
    await page.locator('#overview-clock-start').click();
    await page.locator('#stop-local-clock').waitFor({state:'visible'});
    await page.locator('#stop-local-clock').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    // The US dispatcher uses its own dialog host; verify the same contract
    // against real commands on this disposable session, never the LAN meet.
    await page.goto(urls.us + '/us/dispatcher');
    await page.locator('[data-action="clock"]').waitFor();
    for (const width of [1280, 360]) {
      await page.setViewportSize({ width, height: 960 });
      for (const action of ['config', 'details', 'assign', 'extra', 'draft', 'finish_session']) {
        await page.locator(`[data-action="${action}"]`).click();
        const editor = page.locator('#editor');
        await editor.waitFor({ state: 'visible' });
        assert.equal(await editor.locator('.dialog-actions [data-close]').count(), 1, action + ': footer close/cancel');
        assert.equal(await editor.evaluate(el => el.scrollWidth <= el.clientWidth), true, action + ': no clipped form');
        await screenshot(`us-${action}-modal-${width}`);
        await editor.locator('.dialog-head [data-close]').click();
        await editor.waitFor({ state: 'hidden' });
      }
      await page.locator('[data-action="clock"]').click();
      const editor = page.locator('#editor');
      assert.equal(await editor.locator('.dialog-actions [data-close]').innerText(), 'Cancel');
      assert.equal(await editor.locator('.dialog-head [data-close]').getAttribute('aria-label'), 'Close');
      assert.equal(await editor.evaluate(el => el.scrollWidth <= el.clientWidth), true);
      const original = await editor.locator('[name="time"]').inputValue();
      await editor.locator('[name="time"]').fill('09:41:00');
      page.once('dialog', dialog => dialog.dismiss());
      await editor.locator('.dialog-actions [data-close]').click();
      assert.equal(await editor.isVisible(), true);
      assert.equal(await editor.locator('[name="time"]').inputValue(), '09:41:00');
      await editor.locator('[name="time"]').fill(original);
      await page.keyboard.press('Escape');
      await editor.waitFor({ state: 'hidden' });
    }
    await page.locator('[data-action="clock"]').click();
    await page.locator('#editor [name="time"]').fill('11:32:00');
    await page.locator('#editor [name="running"]').uncheck();
    await screenshot('us-clock-modal-mobile');
    await page.locator('#editor [type="submit"]').click();
    await page.locator('#editor').waitFor({ state: 'hidden' });
    assert.equal((await (await page.request.get(urls.us + '/v1/clock')).json()).time, '11:32:00');
    await page.locator('[data-action="draft"]').click();
    await page.locator('#editor [name="notes"]').fill('Preserve this draft after validation failure');
    await page.locator('#editor .remove-leg').click();
    await page.locator('#editor [type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#editor .form-error')?.textContent.length > 0 && document.querySelector('#editor').dataset.busy === 'false');
    assert.equal(await page.locator('#editor').isVisible(), true);
    assert.equal(await page.locator('#editor [name="notes"]').inputValue(), 'Preserve this draft after validation failure');
    assert.equal(await page.locator('#editor [name="notes"]').isDisabled(), false);
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#editor .dialog-head [data-close]').click();
    assert.equal(await page.locator('#editor').isVisible(), true);
    page.once('dialog', dialog => dialog.accept());
    await page.keyboard.press('Escape');
    await page.locator('#editor').waitFor({ state: 'hidden' });
    // Take over a running internal clock through the actual confirmation UI.
    // Cancel must not pause it; successful takeover must leave it paused on return.
    await page.setViewportSize({width: 1200, height: 900});
    // The two localhost fixtures share the cookie host: restore the EU login.
    await login(urls.eu);
    await page.goto(urls.eu + '/#simulation');
    await page.locator('#simulation-start-open').waitFor({state: 'visible'});
    const context = await (await page.request.get(urls.eu + '/v1/server-context')).json();
    const clockResponse = await page.request.post(urls.eu + '/v1/clock', {data: {
      action: 'start', meet_generation: context.selected_meet.generation,
    }});
    assert.equal(clockResponse.ok(), true);
    await page.locator('#simulation-start-open').click();
    const simulationDialog = page.locator('#simulation-start-modal');
    assert.match(await simulationDialog.innerText(), /Enheterna behåller sina anslutningar/);
    await simulationDialog.getByRole('button', {name: 'Avbryt', exact: true}).click();
    assert.equal((await (await page.request.get(urls.eu + '/v1/clock')).json()).running, true);
    await page.locator('#simulation-start-open').click();
    await page.locator('#simulation-time').fill('09:17');
    await page.locator('#simulation-profile').selectOption('timetable');
    await page.setViewportSize({width: 360, height: 900});
    const confirm = simulationDialog.getByRole('button', {name: 'Pausa spelet och starta simulering', exact: true});
    const bounds = await confirm.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 360, 'Takeover button fits mobile');
    await screenshot('simulation-takeover-mobile');
    const startResponse = page.waitForResponse(response => response.url() === urls.eu + '/v1/simulation' && response.request().method() === 'POST');
    await confirm.click();
    assert.equal((await startResponse).ok(), true);
    await simulationDialog.waitFor({state: 'hidden'});
    let simulation = await (await page.request.get(urls.eu + '/v1/simulation')).json();
    assert.equal(simulation.active, true);
    await page.locator('#simulation-finish-open').click();
    await page.locator('#simulation-confirm-submit').click();
    await page.locator('#simulation-confirm-modal').waitFor({state: 'hidden'});
    simulation = await (await page.request.get(urls.eu + '/v1/simulation')).json();
    assert.equal(simulation.active, false);
    assert.equal(simulation.clock.running, false);
    assert.deepEqual(errors, []);
    assert.ok(requests.every(url => url.startsWith(urls.eu + '/') || url.startsWith(urls.us + '/')), 'Unexpected non-fixture network request');
    console.log('LIVE isolated HTTP/SQLite smoke passed:', JSON.stringify(urls), 'EU/US clocks, chooser, Settings TMBox, Home, TKL setup + Home + Settings return, served i18n asset.');
  } catch (error) {
    console.error('Backend diagnostics:', stderr);
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await new Promise(resolve => fixture.once('exit', resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

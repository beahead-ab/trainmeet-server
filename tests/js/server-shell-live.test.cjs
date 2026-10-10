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
      await page.locator('#login-email').fill('smoke-admin@example.se');
      await page.locator('#login-password').fill('isolated-browser-test');
      await page.locator('#login-form button[type="submit"]').click();
      await page.locator('#overview-view').waitFor({ state: 'visible' });
    }

    await login(urls.eu);
    const smokeRow = page.locator('#device-list tr').filter({hasText: 'TBX-SMOKE'});
    await smokeRow.waitFor();
    assert.equal(await page.locator('#overview-traffic').isVisible(),true);
    // Drift is one screen: the clock, the line, the stations with their boxes,
    // what comes next and the diagram, each once.
    for (const id of ['drift-clock', 'drift-map', 'drift-stations', 'overview-traffic', 'drift-graph']) assert.equal(await page.locator('#' + id).isVisible(), true, id);
    // The fixture box connected once and has not pinged since this server
    // started: no contact, with the time it was last seen. The pairing code is
    // not shown here; boxes never use it.
    const smokeStatus=smokeRow.locator('.kr-tag');
    assert.match(await smokeStatus.textContent(),/^Ingen kontakt · sist sedd \d{2}[:.]\d{2}$/);
    assert.match(await smokeStatus.getAttribute('class'),/\bwarn\b/);
    // Warning colour, not the row's ink.
    const [statusColor,stationColor,statusWeight]=await smokeStatus.evaluate(status=>{const probe=document.createElement('i');probe.style.color='var(--kr-amber)';document.body.append(probe);
      const amber=getComputedStyle(probe).color;probe.remove();return [getComputedStyle(status).color,amber,getComputedStyle(status).fontWeight];});
    assert.equal(statusColor,stationColor);
    assert.equal(statusWeight,'600');
    assert.equal(await page.locator('#overview-graph').isVisible(),true);
    assert.equal(await page.locator('#overview-view details').count(),0,'Drift folds nothing away');
    await page.locator('#overview-clock-start').click();
    await page.locator('#overview-clock-stop').waitFor({state:'visible'});
    assert.equal((await (await page.request.get(urls.eu+'/v1/clock')).json()).running,true);
    await page.locator('#overview-clock-stop').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    assert.equal((await (await page.request.get(urls.eu+'/v1/clock')).json()).running,false);
    for(const width of [1280,360]){
      await page.setViewportSize({width,height:900});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Drift overflow');
      await screenshot('drift-'+width);
    }
    await page.setViewportSize({width:1280,height:960});
    // Left and right on the boxes of a station: a dialog from the stations table, saved against Cloud's version.
    const initialPlacement=await (await page.request.get(urls.eu+'/v1/cloud/presentation')).json();
    const firstStation=initialPlacement.stations[0].station_id;
    const launch=page.locator(`#device-list tr[data-station-id="${firstStation}"] button[title="Ändra vänster och höger"]`);
    await launch.click();
    const placementForm=page.locator('#display-placement-modal');
    await placementForm.locator('select').selectOption('right');
    await placementForm.locator('[type=submit]').click();
    await placementForm.waitFor({state:'hidden'});
    const saved=await (await page.request.get(urls.eu+'/v1/cloud/presentation')).json();
    assert.equal(saved.stations[0].connections[0].side,'right');
    const stale=await page.request.post(urls.eu+'/v1/cloud/display-placement',{data:{publication_id:initialPlacement.publication_id,config_version:initialPlacement.config_version,station_id:initialPlacement.stations[0].station_id,sides:{}}});
    assert.equal(stale.status(),409);
    await launch.click(); await placementForm.locator('select').selectOption('');
    page.once('dialog', dialog => dialog.accept());  // "Stäng utan att spara ändringarna?"
    await placementForm.getByRole('button',{name:'Avbryt',exact:true}).click();
    await placementForm.waitFor({state:'hidden'});
    assert.equal((await (await page.request.get(urls.eu+'/v1/cloud/presentation')).json()).stations[0].connections[0].side,'right');
    const deviceDialog=page.locator('#device-form-modal');
    for(const [width,station] of [[1280,'station-a'],[360,'station-b']]){
      await page.setViewportSize({width,height:900});
      // Waiting for a station: "Välj station"; once assigned: "Redigera". Both open the same dialog.
      await smokeRow.getByRole('button',{name:width===1280?'Välj station ▾':'Redigera',exact:true}).click();
      await deviceDialog.locator('#device-station').selectOption(station);
      await deviceDialog.locator('[type=submit]').click();
      await deviceDialog.waitFor({state:'hidden'});
      assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices[0].station_id,station);
      await page.reload();
      await smokeRow.getByRole('button',{name:'Redigera',exact:true}).click();
      assert.equal(await deviceDialog.locator('#device-station').inputValue(),station);
      await deviceDialog.getByRole('button',{name:'Avbryt',exact:true}).click();
      await deviceDialog.waitFor({state:'hidden'});
    }
    // Clock settings propagate to a separate unauthenticated TV context.
    await page.setViewportSize({width:1280,height:960});
    await page.locator('#clock-adjust').click();
    await page.locator('#local-clock-time').fill('14:26:00');
    await page.locator('#local-clock-speed').fill('4,3');
    await page.locator('#clock-control-form [type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#clock-control-form').dataset.dirty);
    await page.getByRole('link',{name:'Inställningar',exact:true}).click();
    await page.locator('#settings-nav a[href="/installningar#skarmar"]').click();
    await page.locator('#clock-style-tiles [data-value="digital"]').click();
    await page.locator('#meet-clock-seconds').check();
    await page.locator('#clock-appearance-form [type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#clock-appearance-form').dataset.dirty);
    // A 1920 × 1080 window on a 1920 × 1080 screen is a full screen; a smaller window is a window.
    const screenContext=await browser.newContext({viewport:{width:1920,height:1080},screen:{width:1920,height:1080}});
    const clockScreen=await screenContext.newPage();
    clockScreen.on('pageerror',e=>errors.push(e.message));
    await clockScreen.goto(urls.eu+'/display/clock?style=swedish');
    // Hours and minutes large, the seconds small beside them, so that the seconds always fit.
    await clockScreen.locator('.clock-digital .cd-hm').filter({hasText:'14:26'}).waitFor();
    assert.equal((await clockScreen.locator('.clock-digital .cd-ss').textContent()).trim(),'00');
    assert.equal(await clockScreen.locator('.sc-stopped').isVisible(),true);
    const digits=await clockScreen.locator('.clock-digital').boundingBox();
    assert.ok(digits.x>=0&&digits.x+digits.width<=1920,'The digits with seconds fit the screen');
    // The toolbar: a full screen hides it after four seconds and brings it back at a movement; a window keeps it.
    assert.equal(await clockScreen.locator('#display-app').getAttribute('data-chrome'),'fullscreen');
    await clockScreen.mouse.move(960,700);
    await clockScreen.locator('#display-toolbar.hidden-toolbar').waitFor({timeout:9000});
    await clockScreen.mouse.move(900,720);
    await clockScreen.locator('#display-toolbar:not(.hidden-toolbar)').waitFor();
    const windowContext=await browser.newContext({locale:'sv-SE',viewport:{width:1440,height:900},screen:{width:1920,height:1080}});
    const windowScreen=await windowContext.newPage();
    await windowScreen.goto(urls.eu+'/display/clock');
    await windowScreen.locator('#display-loading').waitFor({state:'hidden'});
    assert.equal(await windowScreen.locator('#display-app').getAttribute('data-chrome'),'window');
    await windowScreen.waitForTimeout(4800);
    assert.equal(await windowScreen.locator('#display-toolbar.hidden-toolbar').count(),0,'A window keeps its toolbar');
    assert.match(await windowScreen.locator('#display-fullscreen').textContent(),/^\s*Helskärm/);
    await windowContext.close();
    for(const style of ['analog','digital']){
      await page.locator(`#clock-style-tiles [data-value="${style}"]`).click();
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
        const drawn=Number((await clockScreen.locator('#topology-svg').getAttribute('viewBox')).split(' ')[2]);
        assert.ok(Math.max(...bounds)-Math.min(...bounds)>drawn*0.75,'Two-station TV layout uses the available width');
        // As in the design: rails, stations are bricks, and the trains on the line are listed under the map.
        assert.ok(await clockScreen.locator('#topology-svg.topology-screen .topology-rail').count()>=2,'the line is drawn as rails');
        const brick=clockScreen.locator('#topology-svg .topology-station').first();
        assert.equal(await brick.evaluate(node=>node.tagName),'rect');
        assert.ok(Number(await brick.getAttribute('width'))>=20,'TV stations are bricks');
        assert.equal(await clockScreen.locator('#topology-online').isVisible(),true,'Banöversikt lists the trains on the line under the map');
      }
      if(path==='graph'){
        // The diagram's time window is this screen's own choice, kept in this browser.
        const windowSelect=clockScreen.locator('#display-graph-window');
        assert.deepEqual(await windowSelect.locator('option').evaluateAll(options=>options.map(option=>option.value)),['120','180','360','1440']);
        const before=await clockScreen.locator('#screen-meet .sc-subtitle').textContent();
        await windowSelect.selectOption('1440');
        await clockScreen.waitForFunction(before=>document.querySelector('#screen-meet .sc-subtitle').textContent!==before,before);
        assert.equal(await clockScreen.evaluate(()=>localStorage.getItem('trainmeet.displayGraphWindow')),'1440');
        await windowSelect.selectOption('180');
      }
      if(path==='dashboard'){
        assert.equal(await clockScreen.locator('.dash-status').isVisible(),true,'På linjen just nu says whether traffic keeps to the timetable');
        const cards=await clockScreen.locator('#dashboard-view > *').evaluateAll(nodes=>nodes.map(n=>({top:n.getBoundingClientRect().top,bottom:n.getBoundingClientRect().bottom})));
        assert.ok(cards[0].bottom<=cards[1].top,'Dashboard statistics do not overlap the map');
        // Stress the presentation with four future events. Render an isolated
        // snapshot only; no traffic or timetable records are changed.
        const snapshot=await (await page.request.get(urls.eu+'/v1/display')).json();
        // The list reads the timetable's services: four trains A 23:5x → B, two
        // of them seven minutes late, at Fler (4) so the tallest rows are drawn.
        const eventsFit=await clockScreen.evaluate(snapshot=>{
          const [a,b]=snapshot.stations;
          const numbers=Array.from({length:4},(_,i)=>String(900+i));
          const services=numbers.map((number,i)=>({id:`stress-${number}`,train_number:number,days:'Dagl',train_type:'person',stops:[
            {station_id:a.id,arrival_time:null,departure_time:`23:5${i}`,stop_order:0},{station_id:b.id,arrival_time:'23:59',departure_time:null,stop_order:1}]}));
          const trains=services.flatMap(service=>service.stops.map((stop,i)=>({id:`${service.id}-${i}`,service_id:service.id,train_number:service.train_number,station_id:stop.station_id,arrival_time:stop.arrival_time,departure_time:stop.departure_time})));
          const movement_live={'stress-900-0':{arrival:'none',departure:'departed',departed_seconds:(23*60+57)*60},'stress-901-0':{arrival:'none',departure:'departed',departed_seconds:(23*60+58)*60}};
          renderDashboard({...snapshot,services,trains,movement_live,train_positions:[],connection_states:[],display:{...snapshot.display,deviation_level:4}});
          const card=document.querySelector('.server-dashboard-bottom .display-card');
          const events=[...card.querySelectorAll('.server-event')];
          return {ok:events.length===4 && events.every(row=>row.getBoundingClientRect().bottom<=card.getBoundingClientRect().bottom-8),
            rows:events.map(row=>[row.textContent.replace(/\s+/g,' ').trim(),Math.round(row.getBoundingClientRect().height),Math.round(row.getBoundingClientRect().bottom)]),card:Math.round(card.getBoundingClientRect().bottom)};
        },snapshot);
        assert.ok(eventsFit.ok,`All four upcoming events fit inside the TV card: ${JSON.stringify(eventsFit)}`);
        // Three train rows, or two plus "and N more", must leave room for
        // the late-arrival status instead of clipping it at the card edge.
        for(const count of [3,6]){
          const result=await clockScreen.evaluate(({snapshot,count})=>{
            const [from,to]=snapshot.stations;
            const sample={...snapshot,clock:{...snapshot.clock,time:'14:26:00',running:false},
              train_positions:Array.from({length:count},(_,i)=>({train_number:String(900+i),status:'connection',connection_id:snapshot.connections[0].id,from_station_id:from.id,to_station_id:to.id})),
              routes:Array.from({length:count},(_,i)=>({train_number:String(900+i),station_id:to.id,arrival_time:i===0?'14:20':'14:30'}))};
            syncDisplayClock(sample);
            renderDashboard(sample);
            const card=document.querySelector('.server-dashboard-bottom .dash-card:last-child');
            const status=card.querySelector('.dash-status');
            return {rows:card.querySelectorAll('.dash-row').length,more:card.querySelectorAll('.dash-row--more').length,
              late:status.classList.contains('is-late'),
              fit:[...card.children].every(child=>child.getBoundingClientRect().bottom<=card.getBoundingClientRect().bottom-1)};
          },{snapshot,count});
          assert.deepEqual(result,{rows:3,more:count>3?1:0,late:true,fit:true},'Train rows and late-arrival status fit inside the TV card');
        }
      }
    }
    await screenContext.close();
    await page.setViewportSize({width:1280,height:960});
    await page.goto(urls.eu+'/drift');
    await page.locator('#overview-clock-start').click();
    await page.locator('#overview-clock-stop').waitFor({state:'visible'});
    await page.locator('#overview-clock-stop').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    // Removing a client leaves actual traffic/history untouched.
    const before=await (await page.request.get(urls.eu+'/v1/display')).json();
    const removal=page.locator('#device-remove-modal');
    const openRemoval=async()=>{
      await smokeRow.getByRole('button',{name:'Redigera',exact:true}).click();
      await deviceDialog.locator('#device-remove-open').click();
      await removal.waitFor({state:'visible'});
    };
    await openRemoval();
    await removal.getByRole('button',{name:'Avbryt',exact:true}).click();
    await removal.waitFor({state:'hidden'});
    assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices.length,1);
    await openRemoval();
    assert.match(await removal.textContent(),/kan inte längre styra trafiken/);
    await removal.getByRole('button',{name:'Ta bort klient',exact:true}).click();
    await removal.waitFor({state:'hidden'});
    await smokeRow.waitFor({state:'detached'});
    // Removed, but it was here a moment ago: it is offered back with one
    // click instead of copying its code from the box.
    const trying=page.locator('#device-removed-trying');
    await trying.getByText('Borttagna boxar som försöker ansluta').waitFor();
    await trying.locator('.status-row').filter({hasText:'TBX-SMOKE'}).getByRole('button',{name:'Återanslut',exact:true}).click();
    await trying.locator('select').selectOption('station-a');
    await trying.locator('[type=submit]').click();
    await smokeRow.waitFor();
    assert.equal(await trying.isHidden(),true);
    assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices[0].station_id,'station-a');
    // One box per side at a station: the side is chosen with the station, and shown unless it is both.
    await smokeRow.getByRole('button',{name:'Redigera',exact:true}).click();
    const sideSelect=deviceDialog.locator('#device-side');
    assert.equal(await sideSelect.inputValue(),'both');
    assert.deepEqual(await sideSelect.locator('option').allTextContents(),['Båda sidor','Vänster','Höger']);
    await sideSelect.selectOption('left');
    await deviceDialog.locator('[type=submit]').click();
    await deviceDialog.waitFor({state:'hidden'});
    await smokeRow.locator('.kr-code').filter({hasText:'vänster'}).waitFor();
    assert.equal((await (await page.request.get(urls.eu+'/v1/devices')).json()).devices[0].station_side,'left');
    await smokeRow.getByRole('button',{name:'Redigera',exact:true}).click();
    assert.equal(await sideSelect.inputValue(),'left','the form opens on the side the box has');
    await deviceDialog.getByRole('button',{name:'Avbryt',exact:true}).click();
    await deviceDialog.waitFor({state:'hidden'});
    await openRemoval();
    await removal.getByRole('button',{name:'Ta bort klient',exact:true}).click();
    await removal.waitFor({state:'hidden'});
    await smokeRow.waitFor({state:'detached'});
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
    assert.equal((await screenMenu.locator('summary').textContent()).trim(),'Öppna på skärm');
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
    // ⚙ is nine sections, one at a time. Every part that can be changed is its
    // own form and saves only its own fields; the code for apps and TKL has its
    // own section, Anslutningskod. Farozon is the last one.
    await page.goto(urls.eu+'/installningar#wifi');
    const panels = section => page.locator(`#${section} form.kr-setform`).evaluateAll(forms => forms.map(form => form.getAttribute('aria-label')));
    assert.deepEqual(await panels('skarmar'), ['Klockan', 'Förseningar och för tidiga tåg']);
    assert.deepEqual(await panels('visning'), ['Förseningar i den här webbläsaren']);
    assert.deepEqual(await panels('wifi'), ['Träffens Wi-Fi', 'QR-koder på skärmarna']);
    assert.equal(await page.locator('#kod #connection-code-form').count(),1);
    assert.equal(await page.locator('#admin-view .kr-setsec').last().getAttribute('data-section'),'farozon');
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
    await page.goto(urls.eu+'/installningar#kod');
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
    for (const id of ['drift-map', 'drift-stations', 'overview-traffic', 'drift-graph']) assert.equal(await page.locator('#' + id).isVisible(), false, id);
    await page.locator('#overview-clock-start').click();
    await page.locator('#overview-clock-stop').waitFor({state:'visible'});
    await page.locator('#overview-clock-stop').click();
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
    await page.setViewportSize({width: 1200, height: 900});
    // The two localhost fixtures share the cookie host: restore the EU login.
    await login(urls.eu);
    // Simuleringen är borttagen (4.0): den gamla adressen landar på Drift, och API:t finns inte.
    await page.goto(urls.eu + '/#simulation');
    await page.waitForURL((url) => url.pathname === '/drift', {timeout: 8000});
    assert.equal((await page.request.get(urls.eu + '/v1/simulation')).status(), 404);
    // Obemannade stationer sköts av automatiken i vanlig drift, och Drift säger det.
    const context = await (await page.request.get(urls.eu + '/v1/server-context')).json();
    const enabled = await page.request.post(urls.eu + '/v1/automatic-stations', {data: {
      action: 'enable', enabled: true, meet_generation: context.selected_meet.generation,
    }});
    assert.equal(enabled.ok(), true);
    await page.locator('#drift-stations .kr-tag.auto').first().waitFor({state: 'visible', timeout: 8000});
    assert.match(await page.locator('#drift-stations-meta').textContent(), /sköts av automatiken/);
    await screenshot('drift-automatic-stations');
    // Ta över leder till inställningen där en station lämnas till en box.
    await page.locator('#drift-stations').getByRole('button', {name: 'Ta över', exact: true}).first().click();
    await page.waitForURL((url) => url.pathname === '/installningar' && url.hash === '#obemannade', {timeout: 8000});
    await page.locator('#obemannade').waitFor({state: 'visible'});
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

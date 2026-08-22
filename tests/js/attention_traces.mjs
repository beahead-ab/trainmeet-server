// Replays the snapshot runs dump_attention.cpp performs, through the web
// attention policy, in the format golden_attention.txt holds.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { AttentionController } = require("../../src/tmbox_gateway/web/tmbox-attention.js");

const CDA = "st-cda";
const VST = "st-vst";

const out = [];
function line(text) { out.push(text); }

function atCda() {
  return { station_id: CDA, clock: { time: "09:00", running: true },
           movements: [], active_clearances: [], line_messages: [] };
}

function clearance(id, status, from, to) {
  return { clearance_id: id, movement_id: "movement-421-cda",
           connection_id: "connection-cda-vst", status,
           from_station_id: from, to_station_id: to };
}

function message(id, from) {
  return { message_id: id, connection_id: "connection-cda-vst",
           status: "delivered_to_device", from_station_id: from };
}

function report(label, events) {
  if (events.length === 0) {
    line(`${label} -> (tyst)`);
    return;
  }
  const rendered = events
    .map((event) => (event.subject ? `${event.kind}:${event.subject}` : event.kind))
    .join(" ");
  line(`${label} -> ${rendered} | loudest=${AttentionController.loudest(events)}`);
}

function aShiftFromBootToAnswer() {
  line("");
  line("[en-arbetspass-fran-start-till-svar]");
  const attention = new AttentionController();

  report("link:up", attention.observeLink(true));

  const snapshot = atCda();
  snapshot.active_clearances = [clearance("clr-1", "waiting", VST, CDA)];
  report("boot-snapshot-med-vantande-begaran", attention.observe(snapshot));
  report("samma-igen", attention.observe(snapshot));

  snapshot.active_clearances.push(clearance("clr-2", "waiting", VST, CDA));
  report("ny-begaran-hit", attention.observe(snapshot));

  snapshot.active_clearances[0].status = "approved";
  report("vi-godkanner-clr-1-sjalva", attention.observe(snapshot));

  snapshot.active_clearances.push(clearance("clr-3", "waiting", CDA, VST));
  report("var-egen-begaran-ut", attention.observe(snapshot));

  snapshot.active_clearances[2].status = "rejected";
  report("den-kom-tillbaka-nekad", attention.observe(snapshot));

  snapshot.active_clearances.push(clearance("clr-4", "waiting", CDA, VST));
  report("annu-en-egen-begaran-ut", attention.observe(snapshot));

  snapshot.active_clearances[3].status = "approved";
  report("den-kom-tillbaka-godkand", attention.observe(snapshot));

  snapshot.active_clearances.push(clearance("clr-5", "waiting", VST, CDA));
  report("begaran-hit-som-strax-aterkallas", attention.observe(snapshot));
  snapshot.active_clearances[4].status = "cancelled";
  report("den-aterkallades", attention.observe(snapshot));

  snapshot.active_clearances.push(clearance("clr-6", "waiting", CDA, VST));
  report("egen-begaran-som-strax-gar-ut", attention.observe(snapshot));
  snapshot.active_clearances[5].status = "expired";
  report("den-gick-ut", attention.observe(snapshot));

  snapshot.line_messages = [message("msg-1", VST)];
  report("linjen-ledig-mot-oss", attention.observe(snapshot));

  snapshot.line_messages.push(message("msg-2", CDA));
  report("vart-eget-meddelande-ekar", attention.observe(snapshot));
}

function theServerDropsOutMidShift() {
  line("");
  line("[servern-forsvinner-mitt-i-passet]");
  const attention = new AttentionController();
  report("link:up", attention.observeLink(true));

  const snapshot = atCda();
  report("boot-snapshot", attention.observe(snapshot));

  report("link:down", attention.observeLink(false));
  report("link:down-igen", attention.observeLink(false));
  report("link:up", attention.observeLink(true));

  snapshot.active_clearances = [clearance("clr-9", "waiting", VST, CDA)];
  snapshot.line_messages = [message("msg-9", VST)];
  report("allt-som-hant-under-tiden", attention.observe(snapshot));
}

function reassignmentStartsOver() {
  line("");
  line("[omtilldelning-borjar-om]");
  const attention = new AttentionController();
  attention.observeLink(true);

  const cda = atCda();
  cda.active_clearances = [clearance("clr-1", "waiting", VST, CDA)];
  report("cda-boot", attention.observe(cda));

  attention.forget();

  const vst = atCda();
  vst.station_id = VST;
  vst.active_clearances = [clearance("clr-5", "waiting", CDA, VST)];
  report("vst-boot-efter-forget", attention.observe(vst));
  report("link:up-igen-efter-forget", attention.observeLink(true));

  vst.active_clearances.push(clearance("clr-6", "waiting", CDA, VST));
  report("ny-begaran-till-vst", attention.observe(vst));
}

line("# Genererad av dump_attention.cpp - redigera inte for hand.");
aShiftFromBootToAnswer();
theServerDropsOutMidShift();
reassignmentStartsOver();
process.stdout.write(out.join("\n") + "\n");

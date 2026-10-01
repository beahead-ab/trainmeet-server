/* Draws /tmbox-lab/floden from flows.js, which scripts/tmbox_flows.py writes
   from the server's 16x2 engine. No train state or rule lives here: the page
   only lays out what the engine showed, step by step. */
(function () {
  "use strict";
  const data = globalThis.TMBoxFlows, {drawLCD} = globalThis.TMBoxLCD;
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function screen(box, shown) {
    // A box the step did not change is drawn dimmed, so the eye goes to the one that did.
    const figure = make("figure", "flow-screen" + (shown.changed ? " is-changed" : ""));
    figure.append(make("figcaption", "", `${box.label} · ${box.station}`));
    const frame = make("div", "lcd-frame"), lcd = make("div", "lcd");
    lcd.setAttribute("role", "img");
    drawLCD(lcd, shown.lines);
    frame.append(lcd); figure.append(frame);
    return figure;
  }
  function screens(flow, list) {
    const row = make("div", "flow-screens");
    for (const shown of list) row.append(screen(flow.boxes.find(box => box.id === shown.box), shown));
    return row;
  }
  function head(flow, step, number) {
    const line = make("div", "flow-step__head");
    line.append(make("span", "n", String(number)));
    if (step.wait) {
      line.append(make("span", "flow-wait", `Väntar ${step.wait} s`));
      return line;
    }
    line.append(make("span", "flow-actor", flow.boxes.find(box => box.id === step.box).label));
    const keys = make("span", "flow-keys");
    keys.setAttribute("aria-label", "Tryck " + step.keys.join(" "));
    for (const key of step.keys) keys.append(make("kbd", "", key));
    line.append(keys);
    return line;
  }
  function meanings(flow, step) {
    const list = make("p", "flow-meanings");
    list.append(make("span", "flow-meanings__lead", `Nu på ${flow.boxes.find(box => box.id === step.box).label}:`));
    for (const [key, label] of step.meanings) {
      const item = make("span"); item.append(make("b", "", key), " " + label); list.append(item);
    }
    return list;
  }
  function stepItem(flow, step, number) {
    const item = make("li", "flow-step");
    const text = make("div", "flow-step__text");
    text.append(head(flow, step, number), make("p", "flow-step__caption", step.caption));
    if (step.meanings.length) text.append(meanings(flow, step));
    item.append(text, screens(flow, step.screens));
    return item;
  }
  function flowCard(flow) {
    const card = make("section", "tm-card flow"); card.id = "flow-" + flow.id;
    card.setAttribute("aria-labelledby", card.id + "-title");
    const title = make("h2", "", flow.title); title.id = card.id + "-title";
    const cardHead = make("div", "tm-card__head"); cardHead.append(title, make("span", "tm-tag flow-mode", flow.mode));
    const body = make("div", "tm-card__body");
    body.append(make("p", "tm-prose", flow.intro));
    const start = make("li", "flow-step flow-step--start");
    const text = make("div", "flow-step__text");
    const startHead = make("div", "flow-step__head"); startHead.append(make("span", "n", "0"), make("span", "flow-wait", "Utgångsläge"));
    text.append(startHead, make("p", "flow-step__caption", flow.setup || "Alla boxar står i översikten. Inget tåg är på gång."));
    start.append(text, screens(flow, flow.start));
    const steps = make("ol", "flow-steps"); steps.append(start);
    flow.steps.forEach((step, index) => steps.append(stepItem(flow, step, index + 1)));
    body.append(steps);
    card.append(cardHead, body);
    return card;
  }
  const index = document.querySelector("#flow-index"), flows = document.querySelector("#flows");
  for (const flow of data.flows) {
    const link = make("a", "tm-btn tm-btn--sm", flow.title); link.href = "#flow-" + flow.id; index.append(link);
    flows.append(flowCard(flow));
  }
})();

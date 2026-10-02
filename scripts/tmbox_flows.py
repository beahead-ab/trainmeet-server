"""The TMBox flows page, executed through the real server-16x2 engine.

Every picture on /tmbox-lab/floden is a frame Terminal16Lab produced for a key
press, with the test bench's data (Munkeröd - Charlottendal - Vagnsta) and a
fixed clock. Nothing is drawn by hand: when the engine changes what a box
shows, the page is regenerated, and tests/test_tmbox_flows.py fails until it is.

Print the browser file; nothing touches MQTT, HTTP or a database.
    PYTHONPATH=src python3 scripts/tmbox_flows.py > src/tmbox_gateway/terminal16_web/flows.js
"""
import json
from uuid import uuid4

from tmbox_gateway.terminal16 import Terminal
from tmbox_gateway.terminal16_demo import demo_lab


KEY_ORDER = "#*ABCD"
MODES = {"clearance": "Med klartecken", "direct": "Direkttrafik"}
NOTICE_SECONDS, RECEIPT_SECONDS = 3, 5

# Each flow: id, title, mode, the boxes it shows, when to use it, an optional
# starting point (keys pressed before the first picture, and what they did),
# and the steps. A step is (box, keys, caption) or ("wait", seconds, caption).
# Keys are what the operator presses: digits are buffered in the box like on
# the real one, and "39#" is typing 39 and searching with #.
FLOWS = [
    ("klarera", "Klartecken, avgång och ankomst", "clearance", ["CDA", "VA"],
     "Det vanliga flödet på en sträcka med klartecken: avsändaren begär, mottagaren ger klart, "
     "avsändaren rapporterar avgång och mottagaren tar emot.",
     None, [
        ("CDA", "39", "Skriv tågnumret direkt från översikten. Siffrorna stannar i boxen tills du trycker #."),
        ("CDA", "#", "# söker tåget. 39 ska till Vagnsta; - betyder att inget är begärt än."),
        ("CDA", "#", "# begär klartecken (?). Vagnsta står i översikten och visar förfrågan direkt."),
        ("VA", "#", "Vagnsta ger klart med #. Tecknet blir > hos båda."),
        ("CDA", "#", "Charlottendal rapporterar faktisk avgång med #. Sträckan är upptagen (▶) "
                     "och Vagnsta kan ta emot direkt."),
        ("VA", "#", "Vagnsta tar emot på planerat spår med #. Charlottendal får beskedet "
                    "39 MOTTAGET med mottagarens kod."),
        ("wait", NOTICE_SECONDS, "Efter tre sekunder går Vagnsta tillbaka till översikten av sig själv."),
        ("wait", RECEIPT_SECONDS - NOTICE_SECONDS,
         "Fem sekunder efter ankomsten försvinner beskedet på Charlottendal, utan kvittering. Sträckan är fri."),
    ]),
    ("annat-spar", "Ta emot på ett annat spår", "clearance", ["CDA", "VA"],
     "När tåget ska in på ett annat spår än det planerade.",
     ([("CDA", "39#"), ("CDA", "#"), ("VA", "#"), ("CDA", "#")],
      "39 har fått klart och avgått från Charlottendal mot Vagnsta."), [
        ("VA", "B", "B väljer annat ankomstspår. Boxen börjar på spår 1."),
        ("VA", "D", "C/D bläddrar bland stationens spår."),
        ("VA", "#", "# tar emot på spår 2. Ankomsten registreras på det spåret."),
    ]),
    ("neka", "Neka en förfrågan", "clearance", ["CDA", "VA"],
     "Mottagaren kan inte ta emot tåget just nu.",
     ([("CDA", "39#"), ("CDA", "#")], "Charlottendal har begärt klartecken för 39."), [
        ("VA", "*", "* på förfrågan frågar först: NEKA 39? Inget är ändrat än."),
        ("VA", "#", "# bekräftar. Båda boxarna visar 39 NEKAT och sträckan är fri igen."),
        ("wait", NOTICE_SECONDS, "Vagnsta går tillbaka till översikten efter tre sekunder. "
                                 "Charlottendal behåller beskedet tills någon trycker #."),
        ("CDA", "#", "# kvitterar. Charlottendal kan begära igen senare."),
    ]),
    ("aterta-begaran", "Återta en begäran", "clearance", ["CDA", "VA"],
     "Avsändaren ångrar sig innan mottagaren har svarat.",
     ([("CDA", "39#"), ("CDA", "#")], "Charlottendal har begärt klartecken för 39."), [
        ("CDA", "*", "* frågar först: ÅTER 39? Förfrågan ligger kvar hos Vagnsta tills du bekräftar."),
        ("CDA", "#", "# återtar. Förfrågan försvinner ur Vagnstas kö."),
        ("wait", NOTICE_SECONDS, "Charlottendal går tillbaka till översikten efter tre sekunder."),
        ("VA", "*", "Vagnsta står kvar i den tomma kön (INGA FRÅGOR). * eller B går till översikten."),
    ]),
    ("aterta-klartecken", "Återta ett klartecken före avgång", "clearance", ["CDA", "VA"],
     "Tåget har fått klart men ska inte gå ändå. Efter faktisk avgång går det inte att återta.",
     ([("CDA", "39#"), ("CDA", "#"), ("VA", "#")], "Vagnsta har gett klart för 39."), [
        ("CDA", "*", "* frågar först: ÅTER 39? Klartecknet gäller tills du bekräftar."),
        ("CDA", "#", "# återtar. Vagnsta ser att klartecknet är borta (-)."),
        ("wait", NOTICE_SECONDS, "Charlottendal går tillbaka till översikten efter tre sekunder."),
    ]),
    ("tva-forfragningar", "Två förfrågningar samtidigt", "clearance", ["MUN", "CDA", "VA"],
     "Charlottendal får förfrågningar från båda hållen.",
     None, [
        ("MUN", "93#", "Munkeröd söker 93."),
        ("MUN", "#", "Munkeröd begär klartecken. Charlottendal står i översikten och visar förfrågan direkt."),
        ("VA", "94#", "Vagnsta söker 94."),
        ("VA", "#", "Vagnsta begär också. Charlottendal stannar på förfrågan den visar; räknaren blir 1/2."),
        ("CDA", "D", "D bläddrar till nästa förfrågan i kön, 2/2."),
        ("CDA", "#", "# ger klart för just det visade tåget, 94. Boxen stannar på det tåget."),
        ("CDA", "A", "A öppnar kön igen. Kvar är 93 från Munkeröd."),
        ("CDA", "#", "# ger klart även för 93."),
    ]),
    ("hitta-aktivt", "Hitta ett tåg som har fått klart", "clearance", ["MUN", "CDA", "VA"],
     "Klartecknet kommer medan du gör något annat. B visar alla tåg som pågår.",
     ([("CDA", "17#"), ("CDA", "#"), ("CDA", "B"), ("CDA", "39#"), ("CDA", "#"), ("CDA", "B")],
      "Charlottendal har begärt klartecken för 17 mot Munkeröd och 39 mot Vagnsta, och står i översikten."), [
        ("MUN", "#", "Munkeröd ger klart för 17."),
        ("VA", "#", "Vagnsta ger klart för 39. Charlottendals översikt visar båda och B:Akt2: två aktiva tåg."),
        ("CDA", "B", "B öppnar de aktiva tågen. Klarerade avgångar kommer först; 1/2 visar vilket du ser. "
                     "Det går också att skriva tågnumret direkt."),
        ("CDA", "D", "D går till nästa aktiva tåg, 39 mot Vagnsta."),
        ("CDA", "#", "# rapporterar avgång för just det visade tåget. 17 väntar kvar i listan."),
    ]),
    ("direkt", "Direkttrafik utan klartecken", "direct", ["CDA", "VA"],
     "På en sträcka med direkttrafik reserverar avsändaren själv. Mottagaren får ingen förfrågan.",
     None, [
        ("CDA", "39#", "Charlottendal söker 39. Knappen heter #Sändklar."),
        ("CDA", "#", "# reserverar sträckan (>). Vagnsta ser tåget som aktivt: B:Akt1."),
        ("CDA", "#", "# rapporterar avgång."),
        ("VA", "B", "Vagnsta öppnar det aktiva tåget med B."),
        ("VA", "#", "# tar emot på planerat spår."),
    ]),
    ("tidtabell", "Bläddra i tidtabellen", "clearance", ["CDA"],
     "Välj tåg ur stationens tidtabell i stället för att skriva numret.",
     None, [
        ("CDA", "#", "# från översikten öppnar stationens kommande tåg, med planerad tid."),
        ("CDA", "D", "C/D bläddrar."),
        ("CDA", "B", "B filtrerar, först på ankomster. En ankomst går att välja först när avsändaren har begärt den."),
        ("CDA", "B", "B igen visar bara avgångar. Ett tredje B visar alla tåg."),
        ("CDA", "#", "# väljer tåget. Nu kan du begära klartecken för det."),
    ]),
    ("nej", "När boxen säger nej", "clearance", ["CDA"],
     "Boxen säger varför ett nummer inte går att välja. Inget skickas förrän du trycker #.",
     None, [
        ("CDA", "93#", "93 kommer från Munkeröd men är inte begärt än. En ankomst går inte att välja "
                       "innan avsändaren har begärt den."),
        ("CDA", "#", "# kvitterar beskedet."),
        ("CDA", "123#", "Ett nummer som inte går vid stationen ger INGET TÅG."),
        ("CDA", "#", "# kvitterar."),
        ("CDA", "4", "Har du börjat skriva ett fel nummer, suddar B sista siffran …"),
        ("CDA", "*", "… och * tömmer hela inmatningen. Inget har skickats till servern."),
    ]),
    ("tva-boxar", "Två boxar på samma station", "clearance", ["CDA-V", "CDA-H", "VA"],
     "En station kan ha en box per sida. Varje box hanterar bara tågen på sina sträckor.",
     None, [
        ("CDA-V", "39#", "Vänsterboxen (mot Munkeröd) söker 39, som går mot Vagnsta. Svaret blir ANNAN SIDA."),
        ("CDA-V", "#", "# kvitterar."),
        ("CDA-H", "39#", "Högerboxen hittar tåget."),
        ("CDA-H", "#", "# begär klartecken."),
        ("VA", "#", "Vagnsta ger klart. På Charlottendal visar bara högerboxen tåget."),
    ]),
]


class Flow:
    """One lab, a fixed clock and a stopwatch that only moves when told."""

    def __init__(self, mode, boxes):
        self.lab = demo_lab(mode)
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.time = 0.0
        self.lab.now = lambda: self.time
        if any(box.startswith("CDA-") for box in boxes):
            del self.lab.terminals["DEMO-CDA"]
            self.lab.terminals["DEMO-CDA-V"] = Terminal("cda", side="left")
            self.lab.terminals["DEMO-CDA-H"] = Terminal("cda", side="right")
        self.boxes = boxes
        self.digits = {box: "" for box in boxes}
        self.shown = {}

    def device(self, box):
        return "DEMO-" + box

    def send(self, box, key, **extra):
        device = self.device(box)
        frame = self.lab.frame(device)
        result = self.lab.command(device, {"command_id": uuid4().hex, "view_token": frame["view_token"],
                                           "key": key, **extra})
        if result["status"] != "accepted":
            raise AssertionError(f"{box} {key}: {result['message']}")

    def press(self, box, keys):
        """Press like the operator: the box keeps digits until # (terminal.js, EntryBuffer)."""
        self.time += 1
        for key in keys:
            if key.isdigit():
                self.digits[box] = (self.digits[box] + key)[:5]
            elif self.digits[box] and key == "*":
                self.digits[box] = ""
            elif self.digits[box] and key == "#":
                device = self.device(box)
                number, self.digits[box] = self.digits[box], ""
                self.send(box, "#", train_number=number,
                          entry_context=self.lab.frame(device)["entry"]["context"])
            else:
                self.send(box, key)

    def lines(self, box):
        frame = self.lab.frame(self.device(box))
        if not self.digits[box]:
            return frame["lines"]
        entry = frame["entry"]
        lines = list(entry["lines"])
        cells = list(lines[entry["row"]])
        cells[entry["column"]:entry["column"] + entry["max_length"]] = self.digits[box].ljust(entry["max_length"], "_")
        lines[entry["row"]] = "".join(cells)
        return lines

    def meanings(self, box):
        frame = self.lab.frame(self.device(box))
        labels = (frame["entry"]["labels"] if self.digits[box] else
                  {key: value["label"] for key, value in frame["keys"].items()})
        return [[key, labels[key]] for key in KEY_ORDER if key in labels]

    def screens(self):
        # Every box is read on each step, as a polling client would: reading
        # is what lets the 3 s notices and the 5 s receipt run their course.
        screens = []
        for box in self.boxes:
            lines = self.lines(box)
            screens.append({"box": box, "lines": lines, "changed": self.shown.get(box) != lines})
            self.shown[box] = lines
        return screens


def box_label(flow, box):
    station = flow.lab.engine.config.stations[flow.lab.terminals[flow.device(box)].station]
    side = {"CDA-V": " · vänster", "CDA-H": " · höger"}.get(box, "")
    return {"id": box, "code": station.code, "label": station.code + side, "station": station.name}


def build_flows():
    flows = []
    for flow_id, title, mode, boxes, intro, start, steps in FLOWS:
        flow = Flow(mode, boxes)
        setup = None
        if start:
            for box, keys in start[0]:
                flow.press(box, keys)
            setup = start[1]
        record = {"id": flow_id, "title": title, "mode": MODES[mode], "intro": intro, "setup": setup,
                  "boxes": [box_label(flow, box) for box in boxes], "start": flow.screens(), "steps": []}
        for box, keys, caption in steps:
            if box == "wait":
                flow.time += keys
                record["steps"].append({"box": None, "keys": [], "wait": keys, "caption": caption,
                                        "screens": flow.screens(), "meanings": []})
                continue
            flow.press(box, keys)
            record["steps"].append({"box": box, "keys": list(keys), "wait": None, "caption": caption,
                                    "screens": flow.screens(), "meanings": flow.meanings(box)})
        flows.append(record)
    return {"profile": "server-16x2", "clock": "12:34",
            "stations": [{"code": s.code, "name": s.name} for s in demo_lab().engine.config.stations.values()],
            "flows": flows}


def browser_source():
    return ("// Generated by scripts/tmbox_flows.py from the server-16x2 engine; do not edit.\n"
            "// tests/test_tmbox_flows.py fails when the engine and this file disagree.\n"
            "globalThis.TMBoxFlows = " + json.dumps(build_flows(), ensure_ascii=False, indent=1) + ";\n")


if __name__ == "__main__":
    print(browser_source(), end="")

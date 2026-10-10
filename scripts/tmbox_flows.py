"""The TMBox flows page, executed through the real server-16x2 engine.

Every picture on /tmbox-lab/floden is a frame Terminal16Lab produced for a key
press, with the test bench's data (Sölvmora - Knastebo - Dimmeby) and a
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
# Flows run on the bench's meet with train 55 added, DY -> KNB -> SVM.
THROUGH = {"genomgaende", "flytta-genomgaende"}
NOTICE_SECONDS, RECEIPT_SECONDS = 3, 5

# Each flow: id, title, mode, the boxes it shows, when to use it, an optional
# starting point (keys pressed before the first picture, and what they did),
# and the steps. A step is (box, keys, caption) or ("wait", seconds, caption).
# Keys are what the operator presses: digits are buffered in the box like on
# the real one, and "39#" is typing 39 and searching with #.
FLOWS = [
    ("klarera", "Klartecken, avgång och ankomst", "clearance", ["KNB", "DY"],
     "Det vanliga flödet på en sträcka med klartecken: avsändaren begär, mottagaren ger klart, "
     "avsändaren rapporterar avgång och mottagaren tar emot.",
     None, [
        ("KNB", "39", "Skriv tågnumret direkt från översikten. Siffrorna stannar i boxen tills du trycker #."),
        ("KNB", "#", "# söker tåget och begär klartecken (?) i samma tryck. 39 ska till Dimmeby, som står i "
                     "översikten och visar förfrågan direkt. * återtar så länge Dimmeby inte har svarat."),
        ("DY", "#", "Dimmeby ger klart med #. Tecknet blir > hos båda."),
        ("KNB", "#", "Knastebo rapporterar faktisk avgång med #. Sträckan är upptagen (▶) "
                     "och Dimmeby kan ta emot direkt."),
        ("DY", "#", "Dimmeby tar emot på planerat spår med #. Knastebo får beskedet "
                    "39 MOTTAGET med mottagarens kod."),
        ("wait", NOTICE_SECONDS, "Efter tre sekunder går Dimmeby tillbaka till översikten av sig själv."),
        ("wait", RECEIPT_SECONDS - NOTICE_SECONDS,
         "Fem sekunder efter ankomsten försvinner beskedet på Knastebo, utan kvittering. Sträckan är fri."),
    ]),
    ("annat-spar", "Ta emot på ett annat spår", "clearance", ["KNB", "DY"],
     "När tåget ska in på ett annat spår än det planerade.",
     ([("KNB", "39#"), ("DY", "#"), ("KNB", "#")],
      "39 har fått klart och avgått från Knastebo mot Dimmeby."), [
        ("DY", "B", "B väljer annat ankomstspår. Boxen börjar på spår 1."),
        ("DY", "D", "C/D bläddrar bland stationens spår."),
        ("DY", "#", "# tar emot på spår 2. Ankomsten registreras på det spåret."),
    ]),
    ("neka", "Neka en förfrågan", "clearance", ["KNB", "DY"],
     "Mottagaren kan inte ta emot tåget just nu.",
     ([("KNB", "39#")], "Knastebo har begärt klartecken för 39."), [
        ("DY", "*", "* på förfrågan frågar först: NEKA 39? Inget är ändrat än."),
        ("DY", "#", "# bekräftar. Båda boxarna visar 39 NEKAT med den andra stationen under, och sträckan är fri igen."),
        ("wait", NOTICE_SECONDS, "Efter tre sekunder går båda tillbaka till översikten av sig själva. "
                                 "Inget besked behöver kvitteras. Knastebo kan begära igen senare."),
    ]),
    ("aterta-begaran", "Återta en begäran", "clearance", ["KNB", "DY"],
     "Avsändaren ångrar sig innan mottagaren har svarat.",
     ([("KNB", "39#")], "Knastebo har begärt klartecken för 39."), [
        ("KNB", "*", "* frågar först: ÅTER 39? Förfrågan ligger kvar hos Dimmeby tills du bekräftar."),
        ("KNB", "#", "# återtar. Båda boxarna visar 39 ÅTERTAGET, och förfrågan är borta ur Dimmebys kö."),
        ("wait", NOTICE_SECONDS, "Efter tre sekunder går båda tillbaka till översikten av sig själva."),
    ]),
    ("aterta-klartecken", "Återta ett klartecken före avgång", "clearance", ["KNB", "DY"],
     "Tåget har fått klart men ska inte gå ändå. Efter faktisk avgång går det inte att återta.",
     ([("KNB", "39#"), ("DY", "#")], "Dimmeby har gett klart för 39."), [
        ("KNB", "*", "* frågar först: ÅTER 39? Klartecknet gäller tills du bekräftar."),
        ("KNB", "#", "# återtar. Båda boxarna visar 39 ÅTERTAGET: Dimmeby ska inte ta emot ett tåg som aldrig gick."),
        ("wait", NOTICE_SECONDS, "Efter tre sekunder går båda tillbaka till översikten och sträckan är fri."),
    ]),
    ("tva-forfragningar", "Två förfrågningar samtidigt", "clearance", ["SVM", "KNB", "DY"],
     "Knastebo får förfrågningar från båda hållen.",
     None, [
        ("SVM", "93#", "Sölvmora skriver 93 och trycker #: förfrågan går direkt. Knastebo står i "
                       "översikten och visar den."),
        ("DY", "94#", "Dimmeby begär 94 på samma sätt. Knastebo stannar på förfrågan den visar; "
                      "räknaren blir 1/2."),
        ("KNB", "D", "D bläddrar till nästa förfrågan i kön, 2/2."),
        ("KNB", "#", "# ger klart för just det visade tåget, 94. Boxen stannar på det tåget."),
        ("KNB", "A", "A öppnar kön igen. Kvar är 93 från Sölvmora."),
        ("KNB", "#", "# ger klart även för 93."),
    ]),
    ("hitta-aktivt", "Hitta ett tåg som har fått klart", "clearance", ["SVM", "KNB", "DY"],
     "Klartecknet kommer medan du gör något annat. B visar alla tåg som pågår.",
     ([("KNB", "17#"), ("KNB", "B"), ("KNB", "39#"), ("KNB", "B")],
      "Knastebo har begärt klartecken för 17 mot Sölvmora och 39 mot Dimmeby, och står i översikten."), [
        ("SVM", "#", "Sölvmora ger klart för 17."),
        ("DY", "#", "Dimmeby ger klart för 39. Knastebos översikt visar båda och B:Akt2: två aktiva tåg."),
        ("KNB", "B", "B öppnar de aktiva tågen. Klarerade avgångar kommer först; 1/2 visar vilket du ser. "
                     "Det går också att skriva tågnumret direkt."),
        ("KNB", "D", "D går till nästa aktiva tåg, 39 mot Dimmeby."),
        ("KNB", "#", "# rapporterar avgång för just det visade tåget. 17 väntar kvar i listan."),
    ]),
    ("direkt", "Direkttrafik utan klartecken", "direct", ["KNB", "DY"],
     "På en sträcka med direkttrafik reserverar avsändaren själv. Mottagaren får ingen förfrågan.",
     None, [
        ("KNB", "39#", "Knastebo skriver 39 och trycker #. På direkttrafik reserverar det sträckan "
                       "direkt (>). Dimmeby ser tåget som aktivt: B:Akt1."),
        ("KNB", "#", "# rapporterar avgång."),
        ("DY", "B", "Dimmeby öppnar det aktiva tåget med B."),
        ("DY", "#", "# tar emot på planerat spår."),
    ]),
    ("tidtabell", "Bläddra i tidtabellen", "clearance", ["KNB"],
     "Välj tåg ur stationens tidtabell i stället för att skriva numret.",
     None, [
        ("KNB", "#", "# från översikten öppnar stationens kommande tåg, med planerad tid."),
        ("KNB", "D", "C/D bläddrar."),
        ("KNB", "B", "B filtrerar, först på ankomster. Även en ankomst som ingen har skickat går att välja och flytta hit."),
        ("KNB", "B", "B igen visar bara avgångar. Ett tredje B visar alla tåg."),
        ("KNB", "#", "# väljer tåget. Nu kan du begära klartecken för det."),
    ]),
    ("nej", "När boxen säger nej", "clearance", ["KNB"],
     "Boxen säger varför ett nummer inte går att välja. Siffrorna skickas först när du trycker #.",
     None, [
        ("KNB", "123#", "Ett nummer som inte går vid stationen ger INGET TÅG."),
        ("wait", NOTICE_SECONDS, "Beskedet försvinner av sig självt efter tre sekunder; # eller * stänger det direkt."),
        ("KNB", "4", "Har du börjat skriva ett fel nummer, suddar B sista siffran …"),
        ("KNB", "*", "… och * tömmer hela inmatningen. Inget har skickats till servern."),
    ]),
    ("genomgaende", "Genomgående tåg", "clearance", ["DY", "KNB", "SVM"],
     "Tåg 55 kommer in från Dimmeby och går vidare mot Sölvmora med samma nummer. Numret gäller "
     "det som är på gång: först ankomsten, sedan avgången.",
     None, [
        ("DY", "55#", "Dimmeby begär 55 mot Knastebo; förfrågan går direkt."),
        ("KNB", "#", "Knastebo står i översikten och ger klart."),
        ("DY", "#", "Dimmeby rapporterar avgång. Knastebo kan ta emot direkt."),
        ("KNB", "#", "Knastebo tar emot 55 på planerat spår."),
        ("wait", NOTICE_SECONDS, "Efter tre sekunder går Knastebo tillbaka till översikten."),
        ("KNB", "55#", "Nu gäller numret avgången mot Sölvmora, och förfrågan går direkt."),
        ("SVM", "#", "Sölvmora ger klart."),
        ("KNB", "#", "Knastebo rapporterar avgång. Sölvmora kan ta emot."),
    ]),
    ("flytta-genomgaende", "Genomgående tåg som ingen skickat", "clearance", ["DY", "KNB", "SVM"],
     "Dimmeby tappade bort 55 och skickade det aldrig, men tåget står i Knastebo. I systemet står "
     "det kvar i Dimmeby, så Knastebo flyttar det hit först och skickar det sedan vidare som vanligt "
     "(Benny #170).",
     None, [
        ("KNB", "55#", "Knastebo skriver 55. Tåget har inte kommit hit i systemet, så boxen frågar "
                       "FLYTTA 55 HIT? och begär ingenting av Sölvmora."),
        ("KNB", "#", "# flyttar 55 hit på planerat spår. Dimmebys del räknas som gjord: Dimmeby får beskedet "
                     "55 MOTTAGET och har inte längre 55 att skicka."),
        ("wait", NOTICE_SECONDS, "Efter tre sekunder går Knastebo tillbaka till översikten."),
        ("KNB", "55#", "Nu står 55 här, och numret gäller avgången mot Sölvmora. Förfrågan går direkt."),
        ("SVM", "#", "Sölvmora ger klart."),
        ("KNB", "#", "Knastebo rapporterar avgång. Sölvmora kan ta emot."),
    ]),
    ("placera", "Flytta hit ett tåg som ingen skickat", "clearance", ["SVM", "KNB"],
     "Sölvmora skickade aldrig 93, men tåget kom till Knastebo. Knastebo flyttar det hit "
     "i efterhand, med tidtabellens spår som förslag, och spelet går vidare.",
     None, [
        ("KNB", "93#", "Knastebo skriver 93. Boxen frågar FLYTTA 93 HIT?: # flyttar det till planerat spår."),
        ("KNB", "B", "B väljer ett annat spår i stället. Boxen börjar på spår 1."),
        ("KNB", "D", "C/D bläddrar bland stationens spår."),
        ("KNB", "#", "# flyttar 93 hit, på det valda spåret. Sölvmoras del räknas som gjord."),
    ]),
    ("tva-boxar", "Två boxar på samma station", "clearance", ["KNB-V", "KNB-H", "DY"],
     "En station kan ha en box per sida. Varje box hanterar bara tågen på sina sträckor.",
     None, [
        ("KNB-V", "39#", "Vänsterboxen (mot Sölvmora) söker 39, som går mot Dimmeby. Svaret blir ANNAN SIDA."),
        ("wait", NOTICE_SECONDS, "Beskedet försvinner av sig självt efter tre sekunder."),
        ("KNB-H", "39#", "Högerboxen hittar tåget och begär klartecken direkt."),
        ("DY", "#", "Dimmeby ger klart. På Knastebo visar bara högerboxen tåget."),
    ]),
]


class Flow:
    """One lab, a fixed clock and a stopwatch that only moves when told."""

    def __init__(self, mode, boxes, *, through=False):
        self.lab = demo_lab(mode, through=through)
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.time = 0.0
        self.lab.now = lambda: self.time
        if any(box.startswith("KNB-") for box in boxes):
            del self.lab.terminals["DEMO-KNB"]
            self.lab.terminals["DEMO-KNB-V"] = Terminal("knb", side="left")
            self.lab.terminals["DEMO-KNB-H"] = Terminal("knb", side="right")
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
    side = {"KNB-V": " · vänster", "KNB-H": " · höger"}.get(box, "")
    return {"id": box, "code": station.code, "label": station.code + side, "station": station.name}


def build_flows():
    flows = []
    for flow_id, title, mode, boxes, intro, start, steps in FLOWS:
        flow = Flow(mode, boxes, through=flow_id in THROUGH)
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

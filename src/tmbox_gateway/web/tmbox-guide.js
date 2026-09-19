// Audited against Server 1.8.0 and firmware 0.4.6. Documentation only;
// no requests, device registrations or traffic commands originate here.
globalThis.TMBoxGuide = [
  {title: "Anslut och tilldela station", status: "Finns", steps: [
    "Anslut boxen till träffens Wi-Fi. Automatisk serverupptäckt är förval.",
    "Boxen visar sitt fasta enhets-ID och väntar på tilldelning.",
    "Admin tilldelar station i Server → Inställningar → TMBoxar. Enheten bestämmer inte stationen.",
    "Boxen hämtar assignment, config och snapshot. Trafikknappar väntar på färska data."]},
  {title: "Sök tåg och välj rörelse", status: "Finns", steps: [
    "Skriv högst fem siffror lokalt. B suddar sista siffran; * lämnar sökningen.",
    "A söker på servern. Obs: ESP32 använder idag A, inte #, för själva uppslaget.",
    "En träff öppnar tåget. Flera träffar: C bläddrar och # väljer rörelse. Ingen träff ger ett besked."]},
  {title: "Bläddra utan tågnummer", status: "Finns", steps: [
    "C öppnar första rörelsen från översikten.", "C går vidare till nästa tåg; * återgår till översikten.",
    "D saknar funktion i dagens ESP32-navigation. Det finns ingen separat Tåg ut/Tåg in-växling ännu."]},
  {title: "Ställa upp och förare redo", status: "Finns – föreslås förenklas", steps: [
    "På en avgång med status none skickar A Uppställt.",
    "När servern svarat positioned skickar nästa A Förare redo.",
    "Servern härleder ready. Det är två driftsteg i dagens profil, inte obligatoriska steg i den föreslagna gemensamma modellen."]},
  {title: "Begär, ge klart eller neka", status: "Delvis – hela avgångskedjan har en lucka", steps: [
    "På redo tåg öppnar A motstationsväljaren. C väljer granne, A skickar begäran.",
    "Mottagaren öppnar klareringskorgen med #, C bläddrar, A ger klart och B nekar.",
    "Avsändaren ska sedan kunna bekräfta Avgått. Idag erbjuds både clearance.request och train.departed, men klientens prioritet väljer fortfarande Begär. En lyckad enskild kommandofixtur bevisar därför inte hela flödet."]},
  {title: "Skicka utan klartecken", status: "Saknas som fullständigt ESP32-flöde", steps: [
    "Serverconfig kan ange direkttrafik, men dagens V2 clearance.request skapar fortfarande ett väntande ärende.",
    "Det måste ersättas med serverstyrd reservation utan mottagarbeslut, följt av explicit Avgått. V1/ESP8266 har detta flöde idag."]},
  {title: "Återta före avgång", status: "Saknas i ESP32-knappsatsen", steps: [
    "Servern har clearance.cancel för väntande ärenden, men navigationen har ingen tangent för detta.",
    "Återtagning efter klartecken före avgång behöver också samordnas; dagens V2-server kräver status waiting.",
    "* är lokal Tillbaka på ESP32 och återtar inte ett trafikärende."]},
  {title: "Ta emot på planerat spår", status: "Finns med ett äldre extra steg", steps: [
    "Välj en ankomströrelse. Dagens A-prioritet skickar först Närmar sig och därefter Ankommit.",
    "Ankomst registreras med befintligt faktiskt eller planerat spår. Servern hanterar frigivning.",
    "Närmar sig ska bort ur den föreslagna normala boxprofilen; det är inte genomfört i dagens firmware."]},
  {title: "Ta emot på avvikande spår", status: "Saknas som gemensam atomär åtgärd", steps: [
    "Målet är: välj inkommande tåg → välj faktiskt spår vid avvikelse → bekräfta Ankommit en gång.",
    "Dagens separata spårbyte är inte likvärdigt: ren ankomst erbjuds inte train.track.change och V2 train.arrived använder inte track_id från kommandot.",
    "Servern behöver spara ankomst och faktiskt spår tillsammans. Planerat spår ska finnas kvar."]},
  {title: "Byt spår på avgående tåg", status: "Finns", steps: [
    "När servern tillåter spårbyte öppnar B spårväljaren.",
    "C väljer bland stationens spår, A skickar valet, * går tillbaka utan ändring.",
    "Servern kontrollerar giltigt spår och beläggning. Spårbytet kan ogiltigförklara befintligt klareringsärende."]},
  {title: "Läs linjemeddelande", status: "Kvittering finns; publicering saknar tangent", steps: [
    "# öppnar linjekorgen när ingen klarering har prioritet.",
    "C väljer meddelande, A kvitterar visning. Det är inte ett körtillstånd och frigör inte en upptagen linje.",
    "line.available.publish finns på servern men kan inte väljas på ESP32-knappsatsen."]},
  {title: "Nätfel, nekade kommandon och dubbeltryck", status: "Finns – hårdvaruprov krävs också", steps: [
    "Boxen visar nät-/serverfel och söker anslutningen igen. Gamla trafikkommandon får inte spelas upp som nya.",
    "Vid nekat kommando visas anledning och färskt läge hämtas. Operatören fattar ett nytt beslut.",
    "Efter skärmbyte finns ett 500 ms inmatningslås. Detta är separat från den fysiska knappens debounce."]},
  {title: "Visa klocka och olika displayformat", status: "Fyra format renderas – inte alltid synlig klocka", steps: [
    "Renderaren kan rita 16×2, 20×2, 16×4 och 20×4. Storleken är inte ett produktversionsnummer.",
    "Klockan kommer från servern, men alla detalj-/felvyer reserverar inte utrymme för tiden idag.",
    "Målet är samma trafikbeslut på alla storlekar, med sidindelning för små skärmar och ärligt frånkopplingsbesked."]},
];

// Literal lifecycle messages from TrainMeetTambox8266.ino, not engine states.
// IDs/codes here are examples, never live connection credentials.
globalThis.TMBoxLegacyDeviceScreens = [
  ["Start / enhets-ID", "TRAINMEET TMBOX", "TBX-EXAMPLE"],
  ["Wi-Fi-installation", "INSTALLERA WIFI", "TMBox-Setup"],
  ["Ogiltig serveradress", "FEL SERVERADRESS", "IP + MQTT-PORT"],
  ["Kunde inte spara", "KAN INTE SPARA", "FORSOK IGEN"],
  ["Tilldelning utan V1-panel", "V1-PANEL SAKNAS", "KOLLA SERVERN"],
  ["Väntar på admin", "KOPPLA BOXEN", "TBX-EXAMPLE"],
  ["Hämtar panel", "BOX KOPPLAD", "HAMTAR PANEL..."],
  ["Kommando nekat", "KOMMANDO NEKAT", "HAMTAR NYTT LAGE"],
  ["Serveranslutning borta", "SERVER BORTA", "FORSOKER IGEN"],
  ["Flera upptäckta servrar", "FLERA SERVRAR", "HALL * FOR VAL"],
  ["Söker server", "SOKER SERVER", "TBX-EXAMPLE"],
  ["Ansluter server", "ANSLUTER SERVER", "TBX-EXAMPLE"],
  ["Ingen kvittens / för gammal data", "INGET SERVER-SVAR", "KONTROLLERA LAGE"],
  ["Wi-Fi borta", "NAT SAKNAS", "FORSOKER IGEN"],
  ["Display saknas", "DISPLAY SAKNAS", "KONTROLLERA I2C"],
  ["Knappsats saknas", "KNAPPSATS SAKNAS", "KONTROLLERA I2C"],
  ["Diagnostikbygge: hårdvarutest", "HARDVARUTEST", "TRYCK ALLA 16"],
  ["Diagnostikbygge: tangenttest", "TANGENT", "A"],
];

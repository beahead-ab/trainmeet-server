# Serverns sidor

Servern kör en Cloud-publicerad träff. Ett sidbyte ändrar aldrig träff,
rättigheter eller trafikmotor. Träffens typ bestämmer EU/US-innehållet.

## Deltagare och drift

- `/`: deltagarvyn, även för en inloggad administratör. Klocka, tidtabell,
  banöversikt och anslutningsuppgifter kan läsas utan inloggning. På EU-träffar
  filtrerar ett stationsval på kartan listorna och tidtabellen.
- `/login`: inloggning för trafikledningen. Efter inloggning öppnas `/drift`.
- `/drift`: klockstyrning, klienter, trafikläge och vilka stationer automatiken sköter.
- `/installningar`: träff/Cloud, server, användare, skärmar, språk och uppdatering.
- `/hjalp`: dokumentation och länk till TMBox-provbänken (`/tmbox-lab/`).
- `/tmbox/`: virtuell TMBox, startbar från deltagarvyn.
  Den registreras utan administratörsinloggning men kan inte påverka trafiken
  förrän trafikledningen tilldelat en station på Drift.
- `/us/dispatcher` och `/us/conductor`: de befintliga US-klienterna.

Arbetsytevalet är borttaget. Gamla `/#workspaces` går till `/` och ett sparat
`trainmeet.workspace` ignoreras/rensas. `/#overview`, `/#settings` och
`/#simulation` leder till sina nya sidor (`/#simulation` till `/drift`, sedan
simuleringen togs bort i 4.0). `/tkl/` och dess paketerade filer är
borttagna; TKL körs separat. `/v1/tkl/*` behålls för den separata klienten.

## Träffklocka och skärmar

Alla sidor använder serverns träffklocka. Tid, hastighet, källa, start och stopp
styrs på Drift. Skärmarna räknar lokalt mellan serverns uppdateringar.

Klockans standardutseende och sekundvisning sparas per träff under
Inställningar → Skärmar och klocka. Varje klockskärm kan välja eget utseende
i sin verktygsrad; `trainmeet.displayClockStyle` och
`trainmeet.displayClockSeconds` sparas bara i den webbläsaren. Valet
”Enligt inställningar” följer åter serverns standard. Det ändrar inte tiden.
Den schweiziska urtavlan ritas av samma befintliga kod som tidigare.

`/display/clock`, `/display/dashboard`, `/display/topology` och `/display/graph`
är publika. US har även `/display/territories`. En skärm som öppnas med sin
egen adress (bokmärkt eller i kioskläge i lokalen), en inloggad webbläsare och
en webbläsare med egna sparade skärmval är träffens skärm: den behåller sina
val och sin vanliga verktygsrad.

Gästläget gäller bara besökare som väljer en vy under **Stora vyer** på
deltagarsidan (dit träffens QR-koder leder). Vyn öppnas skrivskyddad i
helskärm, följer träffens inställningar och har bara **Tillbaka** och
**Byt skärm**. Märket ligger i webbläsarhistoriken, så omladdning, bakåt och
framåt behåller gästläget. På klockan svävar knapparna i hörnen och klockan
behåller hela skärmen; på övriga vyer får de en egen remsa ovanför
träffnamnet. Ett tryck på klockans sekunder hos en gäst sparar ingenting.

QR-koder genereras lokalt:
Wi-Fi först (om nätverket är ifyllt), sedan en länk till deltagarvyn på samma
webbadress som skärmen. Inga externa QR-tjänster används.

## Wi-Fi och städning

Nätverksnamn, lösenord och automatisk städtid hanteras under Inställningar.
Wi-Fi-lösenordet lämnar inte administratörsgränssnittet förrän den särskilda
delningsrutan aktiveras. Samma regel gäller publika API-svar och QR-koder.
Ett skyddat nätverk med dolt lösenord visas inte som ett öppet nätverk.

Automatisk städning tar endast bort inaktiva, otilldelade virtuella TMBoxar
(30 minuter som standard, inställbart 5–240). Tilldelade och fysiska boxar
berörs inte. Aktivitet och tilldelning kontrolleras igen i samma transaktion
som borttagningen så att en nyss tilldelad box inte rensas av misstag.

# Server: beslut, implementation och Cloud-kontrakt

Datum: 2026-09-27. Underlag: `trainmeet-cloud-codex-spec.zip`, hela
`SPEC.md` och `KONTEXT.md`, särskilt A8, A9, B6 och API-/leveransdelarna.
Användarens senare bekräftelse gäller framför underlagets öppna fråga:
**TMBox-placering flyttas till träffservern.** Denna arbetsgren gäller bara Server.

## Beslut och varför

| Beslut | Kontext/konsekvens |
|---|---|
| Cloud är planering; Server är träffens körning. | Ingen ny konfliktmotor eller Cloud-redigering byggs i Server. Server visar publicerade fynd, inte utkastets eller den pågående trafikens konflikter. |
| Flera namngivna träffservrar kan tillhöra samma träff. | Användaren har både lokal Pi och hostad testserver. Äldre resonemang om endast en server är ersatt. Varje server rapporterar sin egen aktiva publicering. |
| Vänster/höger bestäms lokalt av administratören. | Boxar tilldelas redan på Server. Cloud levererar standarden; lokala val får inte försvinna när en ny publicering hämtas. |
| Placering är presentation, inte tågets destination. | Ändringen får inte röra panelernas tangentbindningar, sträckor, trafikregler, pågående klareringar eller kvittenser. |
| Tidtabellsfynd är rådgivande metadata. | Strukturvalideringen gäller fortfarande. En konfliktanteckning får inte i sig stoppa aktivering eller trafik. |
| Server-only i denna chat. | Cloud-design, Cloud-roller, Cloud-datamodell och fysisk firmware ändras inte här. Integration beskrivs nedan. |

Tidigare beslut som ska bevaras, inte betraktas som en ny implementeringslista:

- Fysiska och virtuella klienter kan ansluta; admin tilldelar station och behörighet.
- Den gemensamma nya TMBox-vyn har serverstyrda texter och trafikbeslut, lokal
  sifferinmatning, `#` som bekräftelse och `*` för tillbaka/avbryt enligt kontext.
  A–D är funktionstangenter, inte en ny användarstyrd destinationsmappning.
- ESP8266 och ESP32 använder samma grundflöde; kapabiliteter kan skilja sig.
  Basdisplayen är 16×2. Provbänken är isolerad från träffens riktiga trafik.
- Träffens klocka styr simuleringen. Simuleringen kan ta över trafiken efter
  tydlig bekräftelse utan att koppla bort tilldelade TMBoxar/TKL-klienter.
- Redigering sker i dialog med Spara, Avbryt och kryss uppe till höger.
- Historiska önskemål i chatten är inte bevis på att en viss ändring är driftsatt.

## 1. Namn och faktiskt aktiv publicering

Server använder befintliga utgående `/config`-anrop, både vanlig hämtning,
manifest och long-poll. Inget extra heartbeat-anrop eller ny inkommande port.
Följande URL-kodade parametrar tillkommer:

| Fält | Betydelse |
|---|---|
| `name` | Serverns sparade installationsnamn, annars gateway-id; högst 80 tecken. |
| `running_version` | Den faktiskt aktiva publiceringens **publication_id**, inte versionsräknaren. Tom sträng betyder att ingen färdig aktiv publicering kan intygas. |

Kontraktet använder publiceringens identitet eftersom befintliga paket har
`publication_id` men inte Clouds ordinal (exempelvis ”Version 8”). Cloud måste
slå upp identiteten inom den kopplade träffen för att visa rätt versionsnummer.
Det är aldrig mjukvaruversion, `config_version`, senaste nedladdade paket eller
long-poll-parametern `after`. Ett väntande paket kan vara `after` samtidigt som
`running_version` fortfarande anger föregående, säkert aktiverade paket.

EU rapporterar endast när vald publicering, aktiv databaspost och laddad motor
stämmer överens. US rapporterar den valda, installerade publiceringen även innan
ett trafikpass startats. Pågående/avbruten övergång ger tomt värde.

**Cloud-beroende:** ta emot parametrarna, spara `reported_name`, respektera
Clouds eget `display_name` och uppdatera körd publicering utan att förväxla den
med levererad version. Saknat fält från äldre Server och explicit tomt fält är
olika saker. Server ändrar inte `display_name`, `location_note` eller Cloud-roller.

Befintlig paus av automatisk Cloud-synk är oförändrad: då görs inga automatiska
kontaktanrop. Senaste kontakt kan därför bli gammal trots att lokal trafik
fortsätter. Manuell uppdateringskontroll rapporterar kontakt och aktiv version.
Frikopplad närvarorapportering under paus är inte införd här.

## 2. Lokalt presentationslager för TMBox

Inställningar → anslutna klienter innehåller en kompakt stationstabell med
Vänster/Höger och Redigera. Dialogen har ett val per ansluten sträcka:
Följ Cloud, Vänster eller Höger. ”Följ Cloud för alla sträckor” ändrar bara
dialogens utkast tills användaren sparar.

API, endast befintlig adminbehörighet:

- `GET /v1/cloud/presentation`: aktiv publicering, lokal `config_version`,
  stationer, standard-/effektiva sidor samt publicerade fynd.
- `POST /v1/cloud/display-placement` med `publication_id`, `config_version`,
  `station_id` och `sides: {connection_id: "left" | "right"}`.
- Tom `sides` tar bort just stationens lokala val. Utelämnade sträckor följer
  aktuell Cloud-standard. Ingen annan stations val tas bort.
- Inaktuellt publicerings-id eller konfigurationsnummer ger 409; användaren
  ska öppna dialogen igen. Ogiltig station, främmande sträcka eller sida ger 400.

Lagring: SQLite runtime settings, `display_placement:<meet_id>`. Varje val sparar
också andra stationens id. Om Cloud återanvänder sträckans id med annan granne
är det gamla valet inte giltigt. Valet överlever omstart och ny publicering för
samma träff; andra träffar är separerade. Originalpaket och checksumma ändras inte.

Sparande och höjning av `config_version` sker i samma transaktion. Motorobjektets
presentation uppdateras utan att anropa `adopt_config`, som annars återställer
interaktioner. Tilldelade klienter notifieras genom befintliga leveransvägar.
Konfigurationsgenerationen gör att återanslutna klienter kan hämta det nya läget.
Presentationspositioner ingår inte i trafiklägets konfigurationsfingeravtryck.

### Kompatibilitet med gamla paneler

Ny stationbaserad TMBox/terminal16 använder den effektiva sidan och sin lista
över aktiva tåg (B från översikten, C/D växlar). Äldre panelprotokoll har ett fast rutnät med två positioner per sida.
Om ett lokalt val skulle skapa rad 3/4 på en sådan panel behåller **den panelen**
sin Cloud-layout; den får aldrig tyst tappa bort en sträcka. Inställningstabellen
visar då en upplysning. Den nya vyn använder fortfarande det lokala valet.
Ingen fysisk firmware flashas eller uppgraderas av detta arbete.

### Cloud-standardens wire-format

Nuvarande driftpaket skickar `panels`, `slots` och `slot_layout`, inte ett separat
`station_display_placement`. Server härleder standarden från dessa fält och
behåller befintlig stabil fallback när det saknas entydig panelplacering.
Clouds nya schemabaserade härledning ska fortsatt exporteras i det kompatibla
paketet, alternativt krävs ett separat versionssatt kontrakt. Server hittar inte
på ett nytt inkommande fält som nuvarande Cloud aldrig skickar.

## 3. Publicerade kontrolluppgifter

Cloud kan lägga `findings` i det oföränderliga driftpaketet. Server accepterar
listan av poster med exempelvis `key`, `rule`, `level`, `source_rows`, `message`.
Även grupperade listor under `conflicts` och `observations` tolereras. Befintliga
paket utan fältet fungerar; avsaknad av metadata visas inte som en godkänd kontroll.

Inställningar → Cloud-koppling → Tidtabellens kontrolluppgifter visar aktiv
publicerings meddelanden som text, inte HTML. Ett nedladdat men väntande pakets
fynd ersätter inte de aktiva. Ingen egen konfliktberäkning eller ny trafikspärr.
Rättelser görs i Cloud och kommer genom en ny publicering.

## Leveransstatus och verifiering

### Tillägg: överlämning och provbänk

Färdiga underlag att dela:

- [Cloud: exakta API-fält, paket och acceptanstester](handoffs/CLOUD-SERVER-API-2026-09-27.md).
- [TMBox/firmware: kompatibilitet och hårdvaruprov](handoffs/TMBOX-FIRMWARE-2026-09-27.md).

Provbänken `/tmbox-lab/` har nu en isolerad dialog för vänster/höger med samma
validering och sidberäkning som Server. Ingen driftinställning ändras från
provbänken. CAS med testets epoch/placeringsversion hindrar gamla formulär från
att skriva över senare ändringar. Spara/Avbryt/Escape/kryss stöds; standardval
är ett utkast tills det sparas. Trafiknollställning behåller placeringen, nytt
test återgår till standard.

Den gemensamma 16×2-vyn höjer `view_revision` när effektiv placering ändras,
även när trafikläget är oförändrat. Tågval, köer, trafik och `entry.context`
bevaras, så klientens oskickade siffror finns kvar. Provbänkens gamla texter om
saknat firmware-glyfstöd/språkval har rättats utan att hävda hårdvaruverifiering.

Ändringarna ligger lokalt på grenen `codex/server-cloud-contract`, baserad på
`origin/main` 5c716ea (1.12.1). Inget i denna arbetsomgång har pushats eller driftsatts.

Verifiering omfattar backend-regressioner, sparat trafikläge från äldre versioner,
väntande kontra aktiv publicering, US utan startat trafikpass, transaktionsrollback,
behörighet/inaktuella formulär, omstart, Cloud-uppdatering, återanvänt sträck-id,
äldre layout, valfria fynd och isolerade webbläsartester. Fysisk TMBox och skarp
Cloud/Server-integration måste verifieras efter samordnad leverans; enhetstester
eller en lyckad lokal build är inte bevis på driftsättning.

Resultat 2026-09-27:

- Hela Python-sviten efter provbänkstillägget: **975 tester, OK**.
- JavaScript-enhetstester (språk och lokal TMBox-inmatning): **29 tester, OK**.
- Mockad Server-webbläsarregression: **OK**, inklusive 360 px, dialogfel,
  språkbyte och HTML-säker visning av fynd.
- Verklig HTTP/SQLite-webbläsarregression med tillfälliga EU-/US-servrar:
  **OK**, inklusive spara/återläs/avbryt/återställ, 409 för gammalt formulär,
  befintliga klockor, tilldelning och simuleringsövertagande.
- Dialogen visuellt kontrollerad vid 1280 och 360 px. Ingen produktionsdata använd.
- Nytt verkligt HTTP/SQLite-webbläsarprov för TMBox: **OK**, inklusive mobil
  390 px, spara/avbryt/kryss/Escape, inaktuellt formulär, lokal inmatning,
  full tågrörelse, fasta boxpositioner, isolering och verklig virtuell klient.
- Provbänkens nya dialog visuellt kontrollerad vid 1280 och 390 px.
- Den första fullkörningen parallellt med Chrome fick en tvåsekunderstimeout
  i ett befintligt HTTP-behörighetstest. Det passerade separat och därefter
  passerade samtliga 975 tester vid full omkörning utan Chrome-belastning.

### Tillägg: aktiva tåg och gömda klartecken

B från översikten öppnar aktiva tåg; C/D växlar direkt mellan deras åtgärdsvyer.
LCD visar antal och position, även när flera tåg finns på samma sida. Klarerade
egna avgångar prioriteras. A är fortsatt förfrågningskön och nya tåg nås via
inmatning/tidtabell. Efter avgång behålls det valda tåget med # avstängd: nästa
tåg måste väljas uttryckligen. Samma logik används i labb, riktig webbklient och
server-16x2-transport för firmware. Ingen ändring i Cloud eller fysisk firmware.

Verifiering efter navigeringstillägget 2026-09-27:

- Hela Python-sviten: **988 tester, OK** (103 sekunder), inklusive 13 nya
  regressioner för aktiva tåg och två samtidiga klartecken i verklig SQLite-runtime.
- Lokal inmatning och shell-språk: **15 JavaScript-tester, OK**.
- Verklig HTTP/SQLite + Chrome: **OK** på dator och mobil. Två klartecken,
  B/C/D, räknare, avgång, spärrad upprepad avgång, placering och isolering.
- Visuell kontroll vid 1600 och 390 px: rosa skal, blå/vit 16×2-display,
  hela tågidentiteter och klockan kvar. Instruktioner och firmwareunderlag uppdaterade.
- Fysiska ESP-enheter är inte provade. Ändringarna är fortfarande lokala,
  inte pushade eller driftsatta i produktion.

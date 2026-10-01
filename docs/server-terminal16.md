# TMBox: gemensam serverstyrd 16×2-profil

Gäller från TrainMeet Server 1.10.0 tillsammans med TMBox firmware 0.7.0.
Uppdatera servern först. ESP8266 och ESP32 använder samma profil och samma
versionsnummer. En större fysisk display visar tills vidare 16×2-profilen.

## Tre sätt att köra

- `/tmbox/`: riktig webbklient till den server som sidan öppnas från. Den får
  ett eget ID och väntar på administratörens stationstilldelning.
- Fysisk ESP8266/ESP32: upptäcker lokal server och visar serverns bild via
  `tmbox/terminal/device/<id>/...`. Ingen lokal trafik- eller språklogik.
- `/tmbox-lab/`: isolerad provbänk med demodata per webbläsarsession. Samma
  skärm- och tangentlogik, men ingen åtkomst till träffens trafik eller databas.
  Från 1.17.3 serverar Servern den själv även på server.trainmeet.app, så den
  följer med varje serveruppdatering; den fristående tjänsten avvecklas med
  `deploy/terminal16/retire.py`. Foten visar vilken serverversion som svarar.

Hitta dit: i adminhuvudet under **Öppna → TMBox** (Virtuell TMBox och
Provbänk med testdata), på träffens sida under **Kör en station i
webbläsaren**, och från varandra: `/tmbox/` har knappen Provbänk och
provbänken har Virtuell TMBox och Träffens sida.

Båda webbsidorna använder Serverns UI-kit (`web/server-design.css`) runt boxen.
Boxen själv – skal, display och knappsats – är låst i blocket `FRUSET` i
`terminal16_web/style.css` och i `tests/tmbox_case_golden.css`. Den ska se ut
som den fysiska boxen; ändra den bara när hårdvaran ändras. Se
[TMBOX-WEBBKLIENT-DESIGN-2026-09-27.md](TMBOX-WEBBKLIENT-DESIGN-2026-09-27.md).

På en internetexponerad server är publik klientregistrering avstängd som
standard. `TRAINMEET_PUBLIC_CLIENT_ORIGIN=https://server.trainmeet.app`
aktiverar den enbart för exakt angiven HTTPS-origin bakom betrodd lokal proxy.
Administrationen kräver fortfarande inloggning. Exponera aldrig den lokala
MQTT-brokern oskyddad mot internet; använd webbklienten där.

## Minsta möjliga antal knapptryckningar

1. Skriv tågnummer direkt; siffrorna stannar lokalt tills `#`. `B` suddar,
   `*` avbryter lokal inmatning. Översiktens `#` öppnar tidtabellen om ingen
   förfrågan väntar. `C`/`D` öppnar aktiva tåg, eller tidtabellen när inga är aktiva.
2. `#` utför den primära åtgärd som visas, exempelvis begär eller avgå.
   Servern väljer nästa station ur tidtabellen. A–D är inte destinationer.
3. Inkommande förfrågan öppnas automatiskt när terminalen är ledig. `#`
   godkänner, `*` nekar; kön visar antal och `A` återvänder till kön.
4. Klart är inte avgång. Avsändaren bekräftar verklig avgång separat med `#`.
   Återtag är möjligt före avgång, aldrig när tåget lämnat stationen.
5. Mottagaren bekräftar ankomst med `#`; avvikande spår väljs vid mottagandet.
   Ankomst, valt spår och frigivning av sträckan sparas i samma transaktion.
6. Avsändaren ser en kort mottagningsbekräftelse som försvinner automatiskt.
   Avslutat tåg lämnar översikten. Inga extra kvitteringar behövs.

Förfrågningar avbryter inte pågående sifferinmatning. Klockan ligger till
höger på rad två. En ledig översta rad är tom. På översikten öppnar `*`
språkval, `C`/`D` väljer och `#` sparar. Admin kan också ändra boxens språk.
Endast aktuella texter och nödvändiga LCD-specialtecken skickas till enheten.

## Aktiva tåg: återfinn båda klarerade utfarter

Ändring 2026-09-27, lokalt implementerad; separat leverans krävs.

Från översikten öppnar **B aktiva tåg**. C/D växlar direkt mellan deras
åtgärdsvyer, utan nytt tågnummer eller extra `#` för att välja. A behåller
förfrågningskön för obesvarade inkommande begäranden. Exempel med två klartecken:

```text
MUN<17     39>VA
B:Akt2 C/D 12:35
```

Efter B:

```text
MUN<17       1/2
#Avg C/D   12:35
```

Ordning: klarerad egen avgång, inkommande avgånget tåg, egen väntande begäran,
inkommande klarerat tåg, egen redan rapporterad avgång. Tidtabellsordning inom
varje grupp. Framtida tåg och avslutade rörelser ingår inte. Två utfarter på samma
sida eller långa identiteter ryms inte alltid på översiktsraden men är åtkomliga
via listan. Identiteter kapas aldrig; vid behov flyttar räknaren till rad två.
Antal över 99 skrivs `99+` på LCD; API och knappetiketter har exakta antal.

`#` gäller bara explicit valt och fortfarande giltigt tåg. Efter avgång ligger
det tåget kvar valt med `#` avstängd. Nästa tåg väljs uttryckligen med C/D. Om
en annan box återtar klartecknet väljs inget annat automatiskt. Gamla skärmtoken
och dubbla kommando-ID:n kan inte skicka nästa tåg. `*` öppnar återtagning före
avgång; `*` igen avbryter och återgår till samma aktiva vy. Vid ankomst är B
fortfarande ”annat spår”, annars återgår B till översikten.

Nytt valfritt frame-fält: `active: {count, position, movement_id}`. Position är
1-baserad i aktivvyn, annars 0. Ingen vald giltig aktiv rörelse ger null-ID.
Klienter behöver inte tolka fältet: rader, glyfer och `keys` bär hela funktionen.
Ingen ny Cloud-API, firmwaregren eller klientägd trafiklogik behövs.

## Drift och kompatibilitet

### Testa placering utan att ändra träffen

I `/tmbox-lab/` öppnar **Testa vänster/höger** en dialog med ett val per
grannstation. Spara tillämpar testplaceringen; Avbryt, Escape och krysset
stänger utan ändring. Återställ standard är bara ett utkast tills du sparar.
Valen gäller endast den egna provbänkssessionen. Nollställ alla enheter tömmer
trafiken men behåller testplaceringen; Nytt test återgår till standard.

Provbänken och den riktiga serverklienten använder samma beräkning av effektiv
vänster/höger-sida. En presentationsändring höjer `view_revision` utan att
återställa trafik, tågval eller `entry.context`; oskickade siffror behålls.
Gamla öppna placeringsformulär avvisas om testet har återställts eller någon
annan flik sparat en ny placering. Den riktiga `/tmbox/`-klienten har ingen
sådan editor: där ändrar administratören placering i Serverns Inställningar.

Se även [Cloud-kontraktet](handoffs/CLOUD-SERVER-API-2026-09-27.md) och
[firmwareunderlaget](handoffs/TMBOX-FIRMWARE-2026-09-27.md).

### Gemensam trafik och transport

SQLite-stationstjänsten är ensam ägare till trafikärenden för äldre boxar,
nya terminaler och TKL. Äldre protokoll behålls för befintlig firmware, men
deras lokala navigeringsspecifikationer gäller inte den nya 16×2-profilen.
Avsluta aktiva äldre klareringar innan första uppgraderingen till gemensam
trafiklogik. Servern vägrar annars övergången; den raderar inte trafiken.

Kommandon är bundna till enhet, station, träff, skärmversion och unikt ID.
Återanslutning hämtar färskt läge och återspelar inte trafikkommandon.
MQTT skickar nya bilder vid förändring; liveness-kvitton är inte upprepade
stationstilldelningar. Webben hämtar bildstatus utan att skicka varje siffra.

### Knappsatsen: samma i webben som på boxen

Varje tangent i bilden har `acts`: sant om den utför en trafikåtgärd (begär,
ge klart, neka, återta, avgå, ankomst), falskt om den bara byter vad som visas.
Efter ett skärmbyte (ny `view_token` eller ändrade tangenter) väntar bara
tangenter med `acts` i `input_guard_ms`, så ett tryck avsett för förra bilden
inte utför något på den nya. Bläddring, siffror och tågsökning svarar direkt.
En tangent utan fältet räknas som handlande. Inga tangenter tänds eller släcks
i webben: ett tryck som inte kan göra något gör ingenting, precis som på boxen.

Webben väntar som boxen (firmware 0.7.3): `VANTAR PA SVAR` på rad två efter
1,5 s, `INGET SVAR` efter 30 s med siffrorna kvar, och kontakten räknas som
bruten först efter 15 s utan svar. `/tmbox/` hämtar bilden var halv sekund,
lika ofta som servern skickar ändringar till boxarna. Provbänken får varje
ändring direkt; en bruten ström räknas som tappad kontakt först efter 15 s,
en ström som servern stängt (till exempel ett utgånget test) direkt. Gränsen
mot skript är 100 tryck per 10 s och session – ingen människa når den.

903 Python-tester och 59 JavaScript-/webbläsartester passerade inför release.
Firmware måste även kontrolleras på fysisk hårdvara; mjukvarutester ersätter
inte prov av display, kabeldragning, specialtecken och knappsats.

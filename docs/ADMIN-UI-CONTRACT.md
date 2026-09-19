# TrainMeet admin – gemensamt visuellt kontrakt

TrainMeet Server och TrainMeet Cloud ska uppfattas som samma administrativa produkt. Sidebaren är ett gemensamt skal; arbetsytorna följer TrainMeets faktiska adminmönster.

## Visuella källor

- `trainmeet/src/index.css` och `tailwind.config.ts` för tokens, typografi och radier,
- `V2Layout` och `AppLogo` för navigation och varumärke,
- `ui/button` och `ui/card` för kontroller,
- `MeetDataPage` och `SpreadsheetGrid` för tät datahantering,
- `MeetAdminPanel` för formulär, listor, dialoger och destruktiva åtgärder,
- `MeetImportSection`, `MeetStationsSection` och `RuntimePublicationSection` för respektive arbetsflöde.

Referens-URL för visuell verifiering är `https://trainmeet.app/meet/hela-huset-fullt-med-tag-2026/data`. Referensdata ska inte följa med en ren installation.

## Regler

Följande värden är det gällande kontraktet för utseende, struktur och
användarflöde:

- **Accent `#c96442`** är primär åtgärd. Den blå `hsl(220 70% 45%)` används inte längre.
- Radie 12 px för kort, 8 px för fält och knappar, 999 px för chip.
- Apphuvud 56 px, vitt, med 1 px botten-border. Servern har inget byggläge;
  träffkonfigurationen redigeras och publiceras i Cloud.
- Inter för all normal UI-text, serverad lokalt. Monospace för alla tider,
  tågnummer, stationssignaturer, boxkoder och IP-adresser.
- Varm dokumentbakgrund `#faf9f5`, vita kort, tunna neutrala kanter.
- Semantiska tokens; råa färger reserveras för TMBox v2-lådan.
- Täta adminlistor och tabeller utan egna inre scrollcontainrar för formulär.
- Synligt startläge, pågående läge, framgång och konkret fel för varje åtgärd.
- Destruktiva åtgärder kräver ett tydligt bekräftelsesteg.

Cloud äger byggandet av träffkonfigurationen. Server använder samma mönster för lokal synk, lokal drift, TMBox-hårdvara och programuppdatering.

## Dialoger och knappar på Server (2026-09-19)

- Grundvyn visar innehåll/status samt **Lägg till** och **Redigera**. Redigering
  sker i ett namngivet modalfönster, inte i permanent öppna inlineformulär.
- Ett kryss med tillgängligt namn och minst 44 × 44 px träffyta finns uppe
  till höger. Nederst finns en gemensam knapprad: **Avbryt** först, primär
  åtgärd sist. Normala ändringar heter Spara; Bjud in, Koppla, Ta bort och
  operativa US-kommandon behåller sina specifika namn.
- Avbryt, kryss och Escape använder samma avbrytningsväg. Ingen av dem
  sparar. Faktiska osparade fältändringar kräver bekräftelse innan de kastas;
  återställning till ursprungsvärden ger ingen onödig varning.
- Inmatning, åtgärder och stängning spärras medan ett svar på sparandet
  inväntas. Dubbelklick får inte skapa dubbla anrop. Ett valideringsinaktiverat
  Spara blockerar däremot inte Avbryt eller kryss.
- Fel visas i dialogen och behåller inmatningen för rättning/nytt försök.
  Bekräftad framgång stänger dialogen och ger kvitto i grundvyn. Destruktiva
  åtgärder kräver fortsatt sin särskilda bekräftelse.
- Fokus börjar i första redigerbara fältet (annars Avbryt/Stäng), stannar i
  dialogen och återgår till öppningsknappen eller motsvarande sektionsknapp.
- Bakgrundspollning får inte ersätta formulärfält eller en pågående
  stationstilldelning. Mobilvy ska rymma fält och knappar utan sidoklippning.
- Dialoger som bara visar information har **Stäng** och kryss, inte Spara.
  Direkta driftåtgärder, exempelvis Starta/Stoppa på översikten, förblir direkta.

Genomgången omfattar Serverns 12 administrativa dialoger (tid, klockutseende,
servernamn, ny användare, inloggning, återställning, nollställning, Cloud-koppling,
TMBox-tilldelning, TMBox-borttagning, skärmanslutningsuppgifter och redigering
av användare) samt den gemensamma dialogvärden för US Dispatcher/Conductor.
Clouds separata administration och TKL:s trafikgränssnitt ändras inte av detta.

Regression: `server-shell.test.cjs` täcker alla 12 dialoger i desktop/mobil,
avbryt/kryss/Escape, fokus, osparade ändringar, upptagetläge, misslyckat sparande
och nytt försök. `server-shell-live.test.cjs` verifierar riktiga sparanrop mot
tillfälliga HTTP/SQLite-servrar och US-dialogernas formulär och informationsvyer.

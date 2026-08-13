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

- Inter för all normal UI-text; DM Sans 600/700 endast för varumärke och navigationsrubriker.
- Varm dokumentbakgrund, vita kort, tunna neutrala kanter, 16 px radie och diskreta skuggor.
- Semantiska HSL-tokens. Råa färger reserveras för den fysiska Tambox-simuleringen.
- Sticky topprad på 48 px med 70 % bakgrund, blur och saturation.
- Helt runda knappar. Blå är primär åtgärd; amber/orange används inte som knappfärg.
- Täta adminlistor och tabeller utan egna inre scrollcontainrar för formulär.
- Synligt startläge, pågående läge, framgång och konkret fel för varje åtgärd.
- Destruktiva åtgärder kräver ett tydligt bekräftelsesteg.

Cloud äger byggandet av träffkonfigurationen. Server använder samma mönster för lokal synk, lokal drift, Tambox-hårdvara och programuppdatering.

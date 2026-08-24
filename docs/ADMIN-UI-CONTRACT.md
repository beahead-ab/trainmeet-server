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
- Applock 56 px, vitt, med 1 px botten-border. I byggläget ligger en mörk list
  på 50 px ovanför.
- Inter för all normal UI-text, serverad lokalt. Monospace för alla tider,
  tågnummer, stationssignaturer, boxkoder och IP-adresser.
- Varm dokumentbakgrund `#faf9f5`, vita kort, tunna neutrala kanter.
- Semantiska tokens; råa färger reserveras för TMBox v2-lådan.
- Täta adminlistor och tabeller utan egna inre scrollcontainrar för formulär.
- Synligt startläge, pågående läge, framgång och konkret fel för varje åtgärd.
- Destruktiva åtgärder kräver ett tydligt bekräftelsesteg.

Cloud äger byggandet av träffkonfigurationen. Server använder samma mönster för lokal synk, lokal drift, TMBox-hårdvara och programuppdatering.

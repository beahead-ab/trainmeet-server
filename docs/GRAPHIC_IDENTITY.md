# TrainMeet grafisk identitet

TrainMeet Server och TrainMeet iPhone ska upplevas som samma produkt som den
centrala TrainMeet-tjänsten. Servern använder därför inte ett fristående tema.
Den centrala koden i `trainmeet/src/index.css` och de centrala
fullskärmskomponenterna är grafisk källa.

## Gemensamma principer

- Administrativa ytor har varm ljus bakgrund, vita kort, tunna neutrala kanter,
  16 px hörnradie och mycket diskret skugga.
- Primärfärgen är `hsl(220 70% 45%)`; accentytan är
  `hsl(215 60% 96%)`.
- Brödtext och kontroller använder Inter eller närmaste systemfont. Tider och
  tekniska värden använder en monospace-font.
- Fullskärmsvyer använder samma mörka presentation som TrainMeet: svart
  bakgrund, ljus information, tunna linjer och röd markering för aktuell tid.
- Banöversikt, tågdiagram och klocka ska behålla samma proportioner, färglogik
  och beteende när de körs lokalt från Raspberry Pi:n.
- Alla elva analoga klockdesigner samt den digitala designen finns lokalt.

## Typografi

- Inter är serverns gränssnittstypsnitt. Endast vikterna 400, 500, 600 och 700
  används; webbläsaren ska inte behöva syntetisera mellanvikter.
- DM Sans 600/700 används enbart för TrainMeet-namnet och kompakta
  varumärkesmärken. Sid-, kort- och formulärrubriker använder Inter.
- Tekniska värden och tider använder serverns monospace-stack. Stationsnamn,
  tågetiketter och övrig diagramtext använder Inter.
- Administrationsgränssnittet har 14 px som kompakt grundstorlek och 1,5 i
  radavstånd. Mikrorubriker är 12 px, semibold, versala och har 0,1 em
  teckenmellanrum.
- Rubriker på 19 px och större använder `-0.025em` i teckenmellanrum. Knappar
  och flikar använder medium eller semibold i stället för extra feta vikter.

## TMBoxen är ett eget grafiskt objekt

TMBox-simuleringen ska inte göras om till ett vanligt TrainMeet-kort. Den
efterliknar den fysiska lådan med 16×2 LCD, samma skärmbredd som tangentbordet,
rosa kapsling och ett tangentbord med fyra gånger fyra tangenter. Samma
proportioner används i webb- och Swift-versionen.

## Underhåll

När den centrala grafiken ändras uppdateras först de semantiska färgtokensen i
serverns `web/app.css` och i iPhone-appens `TrainMeetTheme.swift`. Funktionella
fullskärmsändringar förs därefter över till den lokala renderingen. Det gör att
utseendet kan utvecklas utan att Raspberry Pi-servern behöver köra React eller
vara internetansluten under träffen.

Det mer konkreta kontraktet för alla adminytor finns i
[`ADMIN-UI-CONTRACT.md`](ADMIN-UI-CONTRACT.md). Det gäller både TrainMeet Server
och TrainMeet Cloud.

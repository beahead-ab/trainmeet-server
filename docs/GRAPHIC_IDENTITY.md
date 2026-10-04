# TrainMeet grafisk identitet

TrainMeet Server och TrainMeet iPhone ska upplevas som samma produkt som den
centrala TrainMeet-tjänsten. Servern använder därför inte ett fristående tema.
Den centrala koden i `trainmeet/src/index.css` och de centrala
fullskärmskomponenterna är grafisk källa.

## Kontrollrummet (2026-10)

Serverns Drift, Inställningar, sidhuvudet, de dialoger som öppnas därifrån,
skärmarna (`/display/…`) och deltagarvyn (`/`) har ett eget mörkt och ljust
läge, "Kontrollrummet". Det är byggt för att man ska se hela träffen på en
blick, på en skärm bredvid banan, och är därför tätare och mörkare än Clouds
varma adminyta. Resten av servern (Hjälp och inloggning) följer tills vidare
principerna nedan.

- Tokens ligger i `web/kontrollrummet.css` (`--kr-*`). Mörkt är grundläget:
  bakgrund `#0d0f13`, paneler `#15181e`, blått för det som är aktivt. Valet
  mörkt/ljust sparas per webbläsare (`trainmeet.theme`) och sätts av
  `web/kr-theme.js` innan sidan målas, så att den inte blinkar till.
- Typsnitten är desamma som ovan, via `--kr-sans` (Inter) och `--kr-mono`
  (JetBrains Mono). `tests/test_fonts.py` håller fast vid det.
- Beräkningarna bakom Drift (var tågen är, stationsrader, nästa händelser,
  diagrammets fönster, sökning) ligger i `web/drift-model.js` som rena
  funktioner med enhetstester i `tests/js/drift-model.test.cjs`. `web/drift.js`
  ritar; `app.js` hämtar data och skickar kommandon.
- En sak visas på ett ställe. Det som sällan behövs (språk på en box, ta bort
  en box, ändra vänster och höger, koppla om med kod) ligger bakom en knapp i
  en dialog och inte som permanent text.
- Mobil: stationslistan blir kort, övriga kolumner göms (`kr-hide-sm`).

### Inställningar

- Sidomeny i tre grupper (Träffen · Den här servern · Webbläsaren) och ett
  avsnitt i taget: Träff och Cloud, Skärmar och klocka, Wi-Fi och QR-koder,
  Namn och nätverk, Anslutningskod, Användare, Programuppdatering, Språk och
  sist Farozon. Adressen är `/installningar#<avsnitt>`; äldre adresser
  (`#anslutning`, `#fynd`, `#klocka`) hamnar i rätt avsnitt. På en smal skärm
  blir menyn en rad överst.
- Varje panel som går att ändra är ett eget formulär (`form.kr-setform`) med
  en egen **Avbryt** och **Spara** längst ned. Båda är släckta tills något
  skiljer sig från det sparade; raden säger då vilka fält som är ändrade
  ("Ändrat: Nätverksnamn, Lösenord"), efter sparandet "Sparat", annars
  "Inget ändrat". Avbryt återställer fälten. `web/settings.js` räknar och äger
  menyn, valen med brickor (klockstil, språk), Wi-Fi-lösenordets Visa/Dölj,
  sökningen i menyn och QR-koderna; formulärens sparande ligger kvar i
  `app.js`.
- Det som sällan behövs eller kräver ett eget beslut ligger kvar i dialog:
  bjud in och redigera användare, byt träff (Cloud-koppling), återställa från
  säkerhetskopia och nollställa träffdata (med skriven bekräftelse), klockkälla
  och klockstyrning. Farozon visar bara vad som händer och öppnar dialogen.
- Det som designen visade men servern inte kan göra (automatisk uppdatering
  av programvaran, loggvy, separat start/stopp-konto, serverns hårdvara,
  fönsterläge) finns inte med; inget i gränssnittet lovar mer än servern gör.

## Gemensamma principer

- Administrativa ytor har varm ljus bakgrund `#faf9f5`, vita kort, tunna
  neutrala kanter, 12 px hörnradie på kort och 8 px på fält och knappar, samt
  mycket diskret skugga.
- Primärfärgen är `#c96442` och accentytan `#f7efe9`. Den varma accenten
  används konsekvent i serverns administrativa gränssnitt.
- Brödtext och kontroller använder Inter eller närmaste systemfont. Tider och
  tekniska värden använder en monospace-font.
- Fullskärmsvyer använder samma mörka presentation som TrainMeet: mörk
  bakgrund, ljus information, tunna linjer och gul markering för aktuell tid
  (se Skärmarna ovan).
- Banöversikt, tågdiagram och klocka ska behålla samma proportioner, färglogik
  och beteende när de körs lokalt från Raspberry Pi:n.
- Alla elva analoga klockdesigner samt den digitala designen finns lokalt.

## Tåg i banöversikten

Samma märke i Drift, på skärmarna och i deltagarvyn, större på en TV:

- Tågnumret i ett litet blått märke med en liten triangel åt färdriktningen
  (som linjeblocket i TrainMeet Cloud).
- **Fylld** när tåget har avgått och är ute på linjen, **ofylld** (vit med blå
  kant) när det har klart men inte har avgått. En begäran utan klart ritas inte.
- Märket ligger en fjärdedel in från stationen tåget lämnar, aldrig på
  stationen eller på ringen runt den. På en TV ligger det på linjen, ovanför
  de stora namnen.
- Tåg inne på en station ritas inte som märken. Siffran efter stationens kod
  (`CDA · 4`) är antalet tåg som står där, och ringen runt stationen betyder
  bara att den är vald eller simulerad. Det är bara det valda tåget som får
  ett märke vid stationen. Märket för ett tåg på linjen glider längs linjen
  om det annars skulle täcka ett stationsnamn.
- Klick på ett tåg i Drift tänder dess rutt och öppnar tågpanelen.

## Skärmarna

Klockan, Banöversikt, Tågdiagram och Översikt ritas på en fast duk om
1920 × 1080 som skalas in i fönstret (`server-ui.js`, `resizeStage`). Utseendet
ligger i `web/skarmar.css`, som laddas efter de äldre stilarken och använder
samma tokens som Drift.

- Verktygsraden (`#display-toolbar`, 52 px) har ← Server, skärmens namn, Byt
  skärm, skärmens egna val (klockans stil och sekunder, diagrammets tidsfönster,
  tåg), Dagl, anslutningen, Mörkt/Ljust och Helskärm.
- Fönsterläge: raden står kvar och duken får det som blir över. Helskärm
  (webbläsarens helskärm, kiosk eller ett fönster lika stort som skärmen):
  raden döljs efter fyra sekunder och kommer tillbaka vid musrörelse, tryck eller
  tangent; den står kvar medan pekaren är över den eller menyn är öppen.
- Skärmens tema är skärmens eget val (`trainmeet.displayTheme`), så att TV:n kan
  vara ljus när driften är mörk. Klockstil, sekunder och diagrammets tidsfönster
  (2, 3, 6 timmar eller hela dygnet) sparas likadant per webbläsare och ändrar
  aldrig den delade klockan.
- Wi-Fi-koden (1) och länken till deltagarvyn (2) står vid klockan uppe till
  höger, i tile-raden på Översikt och i hörnet på klockskärmen. De tar aldrig
  plats från banan eller diagrammet.
- Digitalklockan visar timmar och minuter stort och sekunderna mindre intill,
  så att sekunderna alltid ryms när de är på. En stoppad klocka visar tiden den
  stannade på och en gul ruta säger varför.
- Gult är "nu": linjen i diagrammet, flaggan med klockslaget, stoppad klocka.

## Deltagarvyn

Det QR-koden på skärmarna leder till (`/`), utan inloggning och utan något att
ändra. Telefonen först (390 px): klockan, "På banan nu" (banan som i Drift, stående
med stationernas kod och antal tåg när telefonen hålls upprätt, med antal på
linjen, inne och avvikelser under), "Nästa händelser" (fylld bricka
för ett tåg på väg in, ofylld för en avgång), tidtabellen med sök och
"Hela tidtabellen". Längst ned "Anslut din TMBox", som öppnar ett ark underifrån
med de tre stegen (Wi-Fi, boxen hittar servern, trafikledningen tilldelar
station), och "Starta virtuell TMBox" och "Provbänk". Utseendet ligger i
`web/deltagare.css`; `participant.js` hämtar `/v1/display` och ritar kartan med
samma kod som Drift. På en bredare skärm står samma kolumn mitt på sidan.
Utan eget val följer vyn enhetens ljus eller mörker; väljaren finns i Drift.

## Drift

- Allt som Drift visar syns samtidigt: nyckeltal, banöversikt, stationer och
  boxar, kommande händelser, tågdiagram och kontrolluppgifter. Långa listor
  rullar inom sitt kort.
- Ett tåg eller en station vald i banöversikten, diagrammet, kommande eller
  stationslistan är vald överallt: rutten tänds i banöversikten och diagrammet
  och en flytande panel visar tåget eller stationen. Panelen byter sida så att
  det valda aldrig hamnar bakom den.
- Sökrutan i sidhuvudet hittar tåg och stationer.
- Är servern i US-läge visas inga Drift-paneler, bara US-sammanfattningen.

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

Det som avses nedan är TMBox v2-vyn.

TMBox v2-vyn ska inte göras om till ett vanligt TrainMeet-kort. Den
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

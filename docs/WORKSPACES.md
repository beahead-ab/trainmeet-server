# Serverns arbetsytor

Servern representerar fortfarande en enda Cloud-publicerad träff. Ett byte av
arbetsyta byter bara gränssnitt, aldrig träff, rättigheter eller trafikmotor.

## Navigation

- `/#workspaces`: tillgängliga arbetsytor som illustrerade kort. Serverns
  `available_workspaces` styr vilka val som visas.
- `/#overview`: Drift och administration. Klockstyrning, banöversikt och aktuellt
  trafikläge på samma sida. Tidslinjen och tidtabell/tågrutter kan fällas ut.
- `/#tmbox`: TMBox v2-testklient och dess befintliga flöden, skärmkatalog och
  referens. Kräver EU-träff och administratör, precis som testklientens API.
- `/tkl/`, `/us/dispatcher`, `/us/conductor`: befintliga separata arbetsytor.
- Hamburgermenyn är enda vägen till Inställningar, Skärmar och Byt arbetsyta.
  Hem återgår till den valda arbetsytan även från Inställningar och Skärmar.

Fysisk TMBox-registrering och stationstilldelning ligger kvar i Inställningar.
TMBox v2-testklienten är inte en ny behörighet för en TKL-operatör eller box.
US fortsätter använda Dispatcher för trafikledning; EU:s trafikpanel visas
inte för en US-träff.

## Gemensam träffklocka

Tid och hastighet ändras i en modal under Inställningar → Träffklocka.
Spara behåller klockans gång-/stoppläge; Starta/fortsätt och Stoppa finns direkt
på översikten. Tid och hastighet valideras tillsammans före lagring. Alla
visningar läser serverns klocka, även när internet saknas.

Utseende och sekundvisning har en egen modal i samma inställningskort.
Valet sparas per träff på servern och delas av klockskärmarna. Det överlever
ny Cloud-publicering av samma träff men följer inte med till en annan träff.
Displayens tidigare lokala stil-/sekundkontroller är borttagna; gamla
`trainmeet.clockStyle`, `trainmeet.showSeconds` och `?style=` påverkar inte
längre visningen. Gamla klocklänkar fungerar fortfarande, men följer servern.

## Pensionerad navigation och kod

Den tidigare flikraden Översikt/Trafik/TMBox v2 är borttagen, inte bara dold.
`RUN_TABS`, `RUN_PANELS`, `selectRunTab`, tillhörande händelsehanterare och
`.run-tab`/`.run-tabs`-CSS används inte längre och har tagits bort.
Den separata `#traffic-view` ersätts av `#overview-traffic` i driftöversikten.
Oanvänd CSS för den tidigare inbäddade TKL-terminalen och genvägsknapparna
är också borttagen. `/tkl/` använder fortsatt sin egen layout.

`/#traffic` är en **deprecierad kompatibilitetslänk**: den öppnar driftöversikten
och rullar till trafikdelen. Den behålls för gamla bokmärken, men används inte
i ny navigation. Ingen separat trafikvy eller pollningsloop finns bakom länken.

`trafficTimer`, `startTrafficView`, `stopTrafficView`, `renderTrafficView` och
den extra trafikcachen är borttagna. Kartan, trafiklistorna och tidtabellen
använder samma `state.overviewSnapshot` från översiktens befintliga uppdatering.
Stationsfilter och avvikelsefilter ritar om denna data utan fler nätanrop.
Stationsvalet uppdateras vid Cloud-konfigurationsändring och återgår till
Hela banan om den valda stationen inte längre finns.

TMBox v2:s befintliga uppdateringsloop körs endast när dess arbetsyta är öppen.
Inga trafikprotokoll, lagrade träffdata eller firmware ändras av denna flytt.

# Extern träffklocka på TrainMeet Server

Implementerad lokalt 2026-09-19. Inte driftsatt eller provad mot en riktig träffklocka.

## Anslutning

1. Logga in som administratör på den **lokala TrainMeet Server** som representerar träffen.
2. Öppna **Inställningar → Skärmar och klocka → Klockkälla → Anslut träffklocka**. Nuvarande källa visas direkt i inställningarna; samma dialog kan nås via klockans källnamn på Drift.
3. Välj **FastClock · extern träffklocka** och ange klockans exakta namn och **FastClock URL**. Förvalt är `https://fastclock.azurewebsites.net`; en annan publik HTTPS-basadress till en kompatibel FastClock-installation kan anges. Länken till API:ts `/api/clocks/{klocknamn}/time` byggs av servern.
4. För att bara följa klockan behövs inga styrningsuppgifter. För start/stopp anges FastClock-användare och eventuellt lösenord.
5. Låt hämtningsintervallet vara 2 sekunder om inget annat behövs. Tryck **Spara**.
6. Servern kontrollerar att klockan kan läsas innan inställningen sparas. Vid fel ligger dialogen och uppgifterna kvar. Avbryt och kryss stänger utan att ändra anslutningen.

Alla skärmar, EU/TKL, US dispatcher/conductor och fysiska/virtuella TMBoxar
läser den gemensamma tiden via servern. Ingen webbläsare eller box ansluter
till FastClock själv. Cloud ändras inte.

## Ansvar och beteende

- **Cloud:** planering och publicerad träffconfig. Inga FastClock-inloggningsuppgifter.
- **Server:** klocknamn, källa, styrningsuppgifter, hämtning och gemensamt klockläge.
- **FastClock:** tid, hastighet (exempelvis 5×), paus, start/stopp och veckodag. Tid/hastighet ändras i FastClock när denna källa är vald, inte på TrainMeet Server. Klockans hastighetsfält är skrivskyddat.
- **Skärmar/boxar:** visar serverns tid. Klockuppdateringar skickar snapshots, inte nya stationstilldelningar/config.

Kopplingen (inklusive FastClock URL) sparas per träffidentitet och EU/US-region i serverns lokala databas.
En ny publicering av samma träff behåller den. Ett byte till en annan träff
ärver inte klocknamn eller lösenord. Sena svar från tidigare källa/träff kastas
bort. Klockans veckodag (till exempel `Monday`) visas som **Måndag** i serverns aktuella klock- och dagvyer i stället för *Dag 1*. Den ändrar inte tyst tidtabellens valda trafikdag eller nollställer pågående trafik.

### Meddelanden och orsaker på skärmarna

Klockans läge styr vad som visas (#223). `message` är FastClocks fria
textfält och visas som information i mörkt glas i klockskärmens nedre vänstra
hörn, både när klockan går och när den står. `pauseReason` gäller bara en paus
och `stoppingReason` bara ett stopp; en gående klocka visar aldrig någon orsak.
Platshållare (`None`, `NoReason`, `SelectStopReason`, tomt) betyder att det
inte finns något att säga och visas inte. Kända koder översätts (till exempel
`Lunch` → Lunchpaus, `Derailment` → Urspårning), okända koder visas inte.
Ett stopp visas i rött glas över urtavlan, och `isCompleted` eller pausen
`Closing` visas tyst som «Dagens spel slut». Inget av det tar plats från
klockan.

Vid nätfel eller för gammalt svar visas en varning och senast mottagna tid
fryses. Ingen annan klocka tar tyst över och inget körtillstånd skapas. Servern
försöker läsa igen och följer klockan när kontakten återkommer. Efter omstart
väntar extern klocka på första riktiga svaret.

Byte tillbaka till intern klocka behåller senast mottagna klockslag men lämnar
klockan stoppad. Administratören väljer Starta för att fortsätta.

FastClock på Azure kräver internet från servern. Intern serverklocka fungerar
utan internet. Förlorad FastClock-kontakt är inte samma sak som att en TMBox
tappat sin lokala serveranslutning.

## Referens, säkerhet och protokoll

Porteringen utgår från Lovable-projektets `src/hooks/useFastClock.ts` och
`src/hooks/useMeetClock.ts` i `beahead-ab/trainmeet`:

- `GET https://fastclock.azurewebsites.net/api/clocks/{klocknamn}/time`.
- `PUT …/{klocknamn}/start?user=…&password=…`.
- `PUT …/{klocknamn}/stop?user=…&password=…&reason=…`.

Svaret använder `time`, `speed`, `isRunning`, `isPaused`, `isCompleted`,
`isUnavailable` och stopporsaker. Parametrar URL-kodas. Hämtningen har timeout
och storleksgräns; omdirigeringar tillåts inte. En alternativ HTTPS-basadress stöds för en **kompatibel FastClock-instans**. HTTP, inbyggda inloggningsuppgifter i URL, lokala/privata IP-adresser, interna värdnamn, sökvägar, frågeparametrar och omdirigeringar avvisas. Andra API-protokoll stöds inte.

Styrningsuppgifter skickas bara från servern till denna tjänst via HTTPS vid
uttryckligt start/stopp. Inställnings-API:t returnerar endast `has_password`,
aldrig lösenordet. Leverantörens svarstext/URL exponeras inte i felmeddelanden.
Lokala databasbackuper ska behandlas som känsliga: de kan innehålla klockans
sparade lösenord.

## Verifiering

Automatiska tester använder en ersättning för FastClock, inte någon riktig
träffklocka. De täcker läsning, hastighet/interpolering, paus, midnatt, felaktiga
svar, avbrott, återhämtning, sena svar, lokal lagring, Cloud-uppdatering, träffbyte,
EU/US-separation, behörighet, maskerat lösenord och boxuppdatering utan omtilldelning.
Webbläsartestet täcker dialog, sparfel, återförsök, skrivskydd och mobilbredd.
Liveprov med träffens riktiga klocknamn återstår.

Slutkontroll 2026-09-19: 738 servertester passerade, varav 20 dedikerade tester
för extern klocka. Det isolerade webbläsartestet passerade efter kontroll av
dialogen på både mobil och dator. Inga kommandon skickades till en riktig
FastClock-tjänst, träffserver eller fysisk box under testerna.

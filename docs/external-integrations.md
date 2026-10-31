# Externa trafikintegrationer – förberedande ram

TrainMeet Server har en transportoberoende ram för framtida anslutningar till
externa tidtabells- och trafiktjänster. Ramen innehåller lokala simulatorer,
kontrakt, verifiering och valbar beständig kommandologg. Den registreras inte i serverns driftflöde,
HTTP-API eller Cloud och ändrar ingen befintlig tågklarering.

## Modell

- En adapterimplementation per tjänst; en separat adapterinstans och session per
  kombination av träff, tjänst, konto och station (`Scope`).
- Servern väljer anslutning från den autentiserade användarens stationstilldelning.
  En framtida HTTP-handler får aldrig lita på ett scope som klienten skickat in.
- Kontrollerad identitet vid anslutning och före operationer. En session får inte
  användas för en annan station eller träff.
- Sessionsresurser ägs av adaptern. `close()` frigör dem. För framtida transporter
  innebär det separat cookie/token-lagring eller separat webbläsarkontext.
- Konfiguration är avstängd som standard. Fasaden tillåter endast
  uttryckligen aktiverade sandboxanslutningar med simulerade adaptrar.
- `credential_ref` är en referens till en framtida hemlighetslagring; den är aldrig
  ett lösenord, en cookie eller en token. Skarp hemlighetslagring ingår inte.

`IntegrationService` är inkörsporten. `Adapter` beskriver autentisering, identitet,
tidtabell, tågstatus, kommando och sessionsstängning. `StubAdapter` är ett körbart
exempel med ett syntetiskt tåg. `HttpSandboxAdapter` och `SandboxServer` provar
samma kontrakt över riktig HTTP på `127.0.0.1`. Inga externa adresser eller
tjänstekonton behövs. Testtjänsten tilldelar identitet från sina egna konton och
har separata, tillfälliga cookies. Den binds aldrig till ett externt nätverkskort.
Klienten använder ingen miljöproxy, följer inga omdirigeringar, begränsar
svarsstorlek och har en ändlig socket-timeout. Det är en testtransport, inte
en färdig klient för externa tjänster.

## Kommandon och återkoppling

Varje kommando har ett ID, ett tåg och en åtgärd. Operationer serialiseras per
anslutning, medan andra stationer kan arbeta parallellt. Samma kommando-ID med
samma innehåll skickas bara en gång inom kommandologgens livstid. Återanvändning av
ett ID med annat innehåll avvisas. Tåget måste finnas i anslutningens tidtabell.

En mottagen begäran är inte automatiskt ett genomfört kommando:

| Resultat | Betydelse |
| --- | --- |
| `confirmed` | Begäran accepterades och önskat tillstånd kunde läsas tillbaka |
| `pending` | Begäran accepterades, men önskat tillstånd är ännu inte verifierat |
| `unknown` | Utfallet kan inte fastställas; kommandot återspelas inte automatiskt |

Sessionsförlust under ett kommando kan lämna utfallet okänt. Adaptrar får inte
göra automatiska skrivförsök efter transportavbrott. Reservationen sparas innan
anropet skickas. `SqliteCommandJournal` med en fil behåller reservation och resultat
över processomstarter; standardinställningen använder en SQLite-databas i minnet.
Flera klientprocesser kan dela en lokal loggfil och reservera samma kommando
atomärt. Loggen innehåller endast domän-ID, åtgärd, utfall och UTC-tidsstämplar.

`IntegrationService.reconcile(scope, command_id)` gör endast läsanrop. Om adaptern
har `command_status` måste den kunna koppla en bekräftelse till just kommandots
ID och innehåll samt läsa tillbaka effekten. Ett annat kommandos ändring av samma
tåg räcker inte. Utan sådan möjlighet behålls det osäkra utfallet. En saknad
bekräftelse medför aldrig automatisk omsändning. Fastställda resultat nedgraderas
inte av sena, parallella svar.

`reconcile_pending(scope)` hittar oavslutade kommandon i loggen efter omstart och
stämmer av dem utan att skicka om dem. Det finns ingen bakgrundsschemaläggning;
anropet sker uttryckligen från klienten. En ny inloggning krävs efter omstart.

Använd en separat loggfil på lokal disk och återanvänd den vid omstart. Om filen
raderas, byts, återställs till en äldre backup eller om klienter använder olika
loggfiler försvinner det gemensamma skyddet mot omsändning. Använd samma stabila
scope- och kommando-ID efter återanslutning. En framtida tjänsteadapter måste
verifiera korrelations- och återläsningsregler mot den konkreta tjänsten.
Ramen påstår inte att den ger exakt-en-gång-leverans över processomstarter.

## Kompatibilitetskontroll

Kör en lokal demonstration från repots rot:

```sh
PYTHONPATH=src python -m tmbox_gateway.integrations
PYTHONPATH=src python -m tmbox_gateway.integrations --write-test
PYTHONPATH=src python -m tmbox_gateway.integrations --http --write-test
mkdir -p data
PYTHONPATH=src python -m tmbox_gateway.integrations --http --write-test --journal data/integration-commands.sqlite3
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*integration*.py' -v
```

Standardkontrollen läser session, identitet, tidtabell och tågstatus. Klarering
rapporteras som `not_tested` tills ett explicit sandboxkommando har körts.
Rapporten skiljer mellan `passed`, `failed`, `unsupported` och `not_tested`.
Tomt underlag kan inte godkänna ett statustest. Alla rapporter är märkta
`simulated: true`; de utgör inte verifiering av en extern tjänst.

Varje explicit demonstration använder ett nytt kommando-ID. HTTP-testtjänstens
data är tillfälliga och återställs när demonstrationen avslutas; kommandologgen
kan behållas. Ett historiskt bekräftat kommando i loggen räcker inte för att
godkänna en ny kompatibilitetskontroll: effekten kontrolleras igen mot simulatorn.

Tjänsten äger sin logghandle. Anropa `service.close()` när arbetet har stoppats;
det stänger sessionerna och databasen. Inloggningar sparas aldrig i kommandologgen.

Separata kontraktstester verifierar sessionsisolering, parallella stationer,
återanslutning, dubbelkommandon, identitetsfel, förlorade svar, timeout, begränsad
anropsfrekvens, felaktiga svar och avvisade omdirigeringar. Ett processtest avslutar
klienten direkt efter en extern effekt och verifierar avstämning efter omstart.
Nätverkstester kräver tillåtelse att öppna loopback-portar i testmiljön. En enskild
preflight intygar inte dessa egenskaper hos en ännu okänd tjänst.

## Nästa fas: kvalificera en tjänsteadapter

Uppföljning: [Externa trafikintegrationer: kvalificera en tjänsteadapter (#219)](https://github.com/beahead-ab/trainmeet-server/issues/219).

Arbetet startas genom ett separat utvecklingsärende när en testmiljö och
behörig åtkomst finns tillgängliga. Dokumentera neutralt:

1. Tillgängligt gränssnitt, autentisering, sessioners livslängd och behörighetsmodell.
2. Identifierare för träff, konto, station och tåg samt mappning till TrainMeet.
3. Läsa tidtabeller och status; dokumentera format, tidszoner och statusvärden.
4. Skriva testkommandon och verifiera mottagande respektive verkligt resultat.
5. Funktionsmatris med observerade resultat, datum och gränssnittsversion.
6. Avbrott, återinloggning, samtidiga stationer, begränsningar och resursstädning.

Välj transport efter verifierade förutsättningar: dokumenterat API,
sessionsbaserad HTTP-klient eller automatiserat webbgränssnitt. Kontraktet
förutsätter ingen viss transport. HTTP-klienten som finns nu stöder endast den
lokala, syntetiska tjänsten.

Före skarp aktivering behövs dessutom hemlighetslagring, behörighetskontroll i
serverns inkörsport, tjänstespecifik avstämning, timeout-/resurspolicy,
sessionsförnyelse, övervakning och en tydlig aktiveringsinställning. Automatisk
avstämningsschemaläggning, hantering av loggfilens livscykel och sparade
kompatibilitetsrapporter ingår inte i denna etapp.
Rapporter ska inte innehålla cookies, token, lösenord eller råa felmeddelanden.

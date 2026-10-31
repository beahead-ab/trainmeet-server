# Externa trafikintegrationer – förberedande ram

TrainMeet Server har en transportoberoende ram för framtida anslutningar till
externa tidtabells- och trafiktjänster. Första fasen innehåller enbart en lokal
simulator, kontrakt och verifiering. Den registreras inte i serverns driftflöde,
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
- Konfiguration är avstängd som standard. Fasadens första version tillåter endast
  uttryckligen aktiverade sandboxanslutningar med simulerade adaptrar.
- `credential_ref` är en referens till en framtida hemlighetslagring; den är aldrig
  ett lösenord, en cookie eller en token. Ingen hemlighetslagring ingår i fas ett.

`IntegrationService` är inkörsporten. `Adapter` beskriver autentisering, identitet,
tidtabell, tågstatus, kommando och sessionsstängning. `StubAdapter` är ett körbart
exempel med ett syntetiskt tåg. Inga externa adresser eller tjänstekonton behövs.

## Kommandon och återkoppling

Varje kommando har ett ID, ett tåg och en åtgärd. Operationer serialiseras per
anslutning, medan andra stationer kan arbeta parallellt. Samma kommando-ID med
samma innehåll skickas bara en gång under processens livstid. Återanvändning av
ett ID med annat innehåll avvisas. Tåget måste finnas i anslutningens tidtabell.

En mottagen begäran är inte automatiskt ett genomfört kommando:

| Resultat | Betydelse |
| --- | --- |
| `confirmed` | Begäran accepterades och önskat tillstånd kunde läsas tillbaka |
| `pending` | Begäran accepterades, men önskat tillstånd är ännu inte verifierat |
| `unknown` | Utfallet kan inte fastställas; kommandot återspelas inte automatiskt |

Sessionsförlust under ett kommando kan lämna utfallet okänt. Adaptrar får inte
göra automatiska skrivförsök efter transportavbrott. Ett framtida skarpt flöde
behöver varaktig kommandolagring, avstämning och tjänstespecifik felklassning.
Ramen påstår inte att den ger exakt-en-gång-leverans över processomstarter.

## Kompatibilitetskontroll

Kör en lokal demonstration från repots rot:

```sh
PYTHONPATH=src python -m tmbox_gateway.integrations
PYTHONPATH=src python -m tmbox_gateway.integrations --write-test
PYTHONPATH=src python -m unittest discover -s tests -p 'test_external_integrations.py' -v
```

Standardkontrollen läser session, identitet, tidtabell och tågstatus. Klarering
rapporteras som `not_tested` tills ett explicit sandboxkommando har körts.
Rapporten skiljer mellan `passed`, `failed`, `unsupported` och `not_tested`.
Tomt underlag kan inte godkänna ett statustest. Alla rapporter är märkta
`simulated: true`; de utgör inte verifiering av en extern tjänst.

Separata kontraktstester verifierar sessionsisolering, parallella stationer,
återanslutning, dubbelkommandon, identitetsfel och förlorade svar. En enskild
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
förutsätter ingen viss transport. Ingen av dessa transporter implementeras nu.

Före skarp aktivering behövs dessutom hemlighetslagring, behörighetskontroll i
serverns inkörsport, beständiga kommandon och avstämning efter omstart, timeouts,
resursgränser, sessionsförnyelse, övervakning och en tydlig aktiveringsinställning.
Rapporter ska inte innehålla cookies, token, lösenord eller råa felmeddelanden.

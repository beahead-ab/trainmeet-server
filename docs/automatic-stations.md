# Obemannade stationer i vanlig drift

Gäller från den version som stänger issue #115. Det här är vanlig drift med
träffens egen trafikdatabas. Trafikspelssimulatorn, som körde i en egen
databas, togs bort i Server 4.0; automatiken är det som finns kvar av den.
Störningar och undanställning väljs under Inställningar (se nedan).

## Vad som händer

När träffklockan går sköts varje station utan TMBox eller TKL i arbete
**automatiskt**. Tider räknas i spelminuter.

| Händelse | Villkor |
|---|---|
| Begär avgång | Tidigast 2 spelminuter före avgång. Tåget står på stationen: tågloppets första avgång, eller föregående del har ankommit. |
| Ge klart | Det planerade mottagningsspåret är ledigt. |
| Avgång | Klartecken eller direktklarering finns och avgångstiden (efter planerat uppehåll) har nåtts. |
| Ankomst | Faktisk avgång plus tidtabellens gångtid har passerat. |
| Nekad begäran | Upprepas inte. En människa får ta ställning. |

- Tåg vars första avgång ligger före den tid då automatiken först körde under
  trafikdagen skickas inte. De är historia, inte en kö som ska skickas på en gång.
- Automatiken arbetar genom samma stationskommandon som en box, med aktören
  `automatik`. Stationstjänsten nekar den på en bemannad station.
- Avgångs- och ankomsttider sparas i `automatic_events` i samma transaktion
  som trafikändringen, vem som än anmälde dem.

## Bemanning

- Den första TMBox eller TKL som arbetar med stationen gör den **manuell**.
  En TMBox räknas via sin tilldelning, en TKL via stationen den läser eller
  manövrerar. Inget trafikpass behövs.
- Tappar operatören kontakten i 45 verkliga sekunder står stationen som
  **Kontakt saknas – väntar**. En annan box eller TKL på samma station som
  fortfarande hörs håller stationen bemannad.
- **Tappad kontakt** under Inställningar → Obemannade stationer: av som förval,
  då väntar stationen på sin operatör. Väljer admin 2, 5 eller 10 minuter tar
  automatiken över en station som varit utan kontakt så länge (verkliga
  minuter, räknat tidigast från serverns start). Kommer operatören tillbaka
  står stationen kvar hos automatiken tills den tas tillbaka.
- Bemanningen sparas per publicering och trafikdag och överlever en omstart.
  Efter omstart står en bemannad station som Kontakt saknas tills boxen hörs av.

## Lämna till automatiken och ta tillbaka

Casper, 2026-10-10: "jag går på toa ett tag". En station kan lämnas till
automatiken även när en box eller TKL är ansluten, och tas tillbaka efteråt.

| Var | Lämna till automatiken | Ta tillbaka |
|---|---|---|
| TMBox, webb-TMBox, iPhone | `*` på startbilden, **AUTOMATIK?**, `#` | `#` på startbilden, **TA TILLBAKA?**, `#` |
| TKL | Meny → Lämna till automatiken | Meny → Ta tillbaka stationen |
| Drift | **Automatik** på stationens rad | **Aktiv** (ger stationen till en ansluten box eller TKL) |
| Inställningar → Obemannade stationer | Lämna till automatiken | Ge tillbaka till … |

- Valet står kvar, också över dygnsskiftet, tills någon tar tillbaka stationen.
  Förut tog en ansluten box tillbaka stationen vid nästa trafikdag.
- Medan automatiken sköter stationen visar boxen **AUTOMATIK** och stationens
  kod på startbilden. Bläddring och kön fungerar. En trafiktangent frågar först
  **TA TILLBAKA?**; efter `#` står boxen kvar på samma tåg och tangenten kan
  tryckas igen. TKL får `409 station_automatic` och frågar på samma sätt.
- Vilken box eller TKL som helst på stationen kan ta tillbaka den. Den som tar
  tillbaka blir stationens operatör, och alla klienter där arbetar som vanligt.
- Är automatiken avstängd erbjuds inte `*`, och en station som lämnats står
  som bemannad igen.
- Varje byte skrivs i audit-loggen (`automatic.station_released`,
  `automatic.station_taken_back`) med vem som gjorde det.

## Av och på

Automatiken är på som standard och stängs av under **Inställningar → Obemannade
stationer**. Den gör ingenting medan klockan står still. Drift visar vilka
stationer automatiken sköter ("Automatisk") och har knapparna Automatik och
Aktiv på stationens rad. En station som bara sköts av sin TKL står som
**Bemannad** (förut "Obemannad", eftersom Drift bara listade boxar).

## Ta emot ett tåg med tvång

Glömmer en station att klarera och skickar bara tåget, skriver mottagaren
tågnumret och svarar `#` på **FLYTTA 93 HIT?**. Se TMBox-flödet "Flytta hit ett
tåg som ingen skickat".

En TKL får grannstationernas läge i `/v1/tkl/context` som `station_modes`
(`automatic`, `manual` eller `disconnected` per granne; tomt när automatiken är
avstängd), så att ställverket kan visa vem som svarar i andra änden.

Samma kontext har stationens eget läge som `automatic`:
`{available, active, released_by}`. `available` är falskt när automatiken är
avstängd; `released_by` är `admin`, `operator` eller `lost_contact`.

TKL: `POST /v1/tkl/automatic {meet_generation, station_id, automatic: true|false}`
lämnar stationen eller tar tillbaka den. Bara terminalens egen station; svaret
är `{automatic: {...}}` som i kontexten.

TMBox-bilden har `station_mode` (`automatic` eller `manned`) för appar som vill
visa läget bredvid displayen.

Admin-API: `GET /v1/automatic-stations`, `POST /v1/automatic-stations` med
`action` = `enable` (`enabled`), `automatic` (`station_id`, `confirmed: true`),
`manual` (`station_id`, `device_id`, `confirmed: true`) eller `lost_contact`
(`minutes`: 0, 2, 5 eller 10). Svaret har `lost_contact_minutes` och per station
`released_by`.

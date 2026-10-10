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
| Avgång | Klartecken eller direktklarering finns och avgångstiden (efter planerat uppehåll) har nåtts. Ett försenat tåg står 2 spelminuter efter sin verkliga ankomst, eller tidtabellens uppehåll om det är kortare, och går sedan. |
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
  **Kontakt saknas – väntar**, eller **Kontakt saknas – automatik om N min**
  när admin valt att automatiken tar över (se nästa punkt). En annan box eller TKL på samma station som
  fortfarande hörs håller stationen bemannad.
- **Tappad kontakt** under Inställningar → Obemannade stationer: **Aldrig –
  stationen väntar på sin operatör** som förval. En station går då aldrig till
  automatiken av sig själv. Väljer admin 2, 5 eller 10 minuter tar
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

## Försenade tåg, störningar och tåg som slutar

Under **Inställningar → Obemannade stationer → Störningar och undanställning**:

- **Försenat tåg:** hur länge ett försenat tåg står vid en automatisk station
  innan det klareras vidare, 1–10 spelminuter, förval 2. Tidtabellens uppehåll
  gäller om det är kortare. Ett tåg i tid följer tidtabellen. Tiden räknas från
  den verkliga ankomsten (före 4.2 kunde ett tåg som kom in sent gå samma
  sekund, eftersom planerad ankomst användes).
- **Störningar:** av som förval, normal eller många, vid stationen, på linjen
  eller båda. Samma scenarionyckel ger samma störningar.
- **Undanställning:** ett tåg som slutar vid en automatisk station ställs undan
  efter 5 spelminuter (valbart), så att spåret blir fritt.

Ett tåg som slutar på en **bemannad** station ställer operatören undan:

| Var | Hur |
|---|---|
| TMBox, webb-TMBox, iPhone | Tåget står bland de aktiva tågen (`B`) som **101 SLUTAR HÄR**; `#` ställer undan det och boxen kvitterar **UNDANSTÄLLT**. |
| TKL | `POST /v1/tkl/stable` (nedan). Tågkortet med "Slutar här – ställ undan" och knappen Ställ undan kommer i en egen TKL-version. |

Ett undanställt tåg håller inte längre sitt spår, varken på en automatisk
eller en bemannad station. Drifts tågpanel säger **Undanställt i LEK**,
`/v1/display` har dem i `stabled` (rörelse-id), och audit-loggen har
`train.stabled` med vem som gjorde det.

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

TKL: `POST /v1/tkl/stable {meet_generation, station_id, movement_id}` ställer
undan ett tåg som slutat på terminalens station och har kommit in (annars
`409 stabling_rejected` med skälet). Medan automatiken sköter stationen svarar
den `409 station_automatic`. Svaret och `/v1/tkl/context` har `stabled`:
`{movement_id: vem}` för stationens undanställda tåg (`automatik` eller
klientens id).

TMBox-bilden har `station_mode` (`automatic` eller `manned`) för appar som vill
visa läget bredvid displayen.

Admin-API: `GET /v1/automatic-stations`, `POST /v1/automatic-stations` med
`action` = `enable` (`enabled`), `automatic` (`station_id`, `confirmed: true`),
`manual` (`station_id`, `device_id`, `confirmed: true`) eller `lost_contact`
(`minutes`: 0, 2, 5 eller 10). Svaret har `lost_contact_minutes` och per station
`released_by` och `takes_over_in`: verkliga sekunder tills automatiken tar över
en station utan kontakt, eller `null` (Aldrig, eller stationen har kontakt).
`action` = `disturbance` tar också `late_stop_minutes` (1–10).

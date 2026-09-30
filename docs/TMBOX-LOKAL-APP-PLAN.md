# Lokal app i TMBox — plan

Status: förslag, ej beslutat. Skriven 2026-09-30.

## Uppdatering 2026-09-30, kväll

Fältfelet är återskapat och förstått, se `tools/tmbox-rig/README.md`. Boxens
fråga "finns servern?" besvarades av samma kö som gjorde allt arbete. När
servern var långsam per meddelande räckte en enda störning för att alla boxar
skulle dö samtidigt och sedan fortsätta dö efter exakt två pingar per session.
Servern svarade hela tiden, bara för sent, och journalen var ren.

Byggt på servern, utan att boxarna behöver flashas om:

- Livstecknet besvaras innan kön, utan trafiklås och utan databas.
- Arbete från ersatta sessioner hoppas över, och bara den senaste pingen per
  box arbetas.
- Journalen säger när kön ligger efter och när en box kopplar upp igen - den
  första halvan av steg 0.

Kvar av steg 0 är raden per box i webbgränssnittet. Kvar av grundproblemet är
att allt arbete fortfarande går genom en enda kö; en långsam server ger långa
knapptryck även om den inte längre fäller boxarna.

## Problemet i en mening

Servern målar skärmen och boxen är ett fönster med femton sekunders tålamod.
Därför blir varje serverhicka ett totalt avbrott för alla boxar samtidigt, och
varje knapptryck en resa över nätet innan något syns.

Det är inte en bugg. Det är följden av en riktig arkitekturvalsituation som vi
valde åt ena hållet, och som vi nu har underlag för att välja om.

### Hur felet ser ut idag

Boxen skickar `presence` var femte sekund och väntar på `alive`. Uteblir svar i
femton sekunder släpper den allt, kopplar ner, gör om mDNS-sökningen från
början och ansluter på nytt. Mätt i fält: en session hinner med exakt två
pingar innan den dör, och återanslutningen tar mellan tolv och femtioen
sekunder. Under tiden står det SOKER SERVER och boxen är obrukbar.

Tre separata brister staplar sig:

1. Boxen har ingen egen bild av trafiken, så utan server har den ingenting att visa.
2. Tålamodet är för kort och binder ihop "har kontakt" med "har data".
3. Återanslutningen kastar bort värd och port och börjar om med mDNS.

## Steg 0 — se felet utan att någon står vid en MQTT-klient

Innan något byggs om: serverns v1-terminalväg loggar ingenting alls. Den tar
emot hello, presence och kommandon utan att lämna ett spår. Det är därför vi
fått räkna nonce-nummer i MQTT Explorer för att avgöra om servern svarar.

Litet, bara server, ingen firmware:

- Loggrad när en box ansluter, när en session byts ut, när ett kommando
  kvitteras och när en box försvinner.
- En rad i webbgränssnittet per box: senast hörd, senast besvarad, aktuell
  session, firmwareversion. Det servern redan vet, synligt.

Då ser du själv vid träffen vad som händer, och vi behöver aldrig mer be någon
öppna en MQTT-klient.

## 1. Vad du får

- **Knapptryck svarar direkt.** Bläddra, välja tåg, välja spår, välja granne,
  slå upp ett tågnummer — allt sker i lådan utan ett enda paket på nätet.
- **En tyst server släcker inte boxen.** Skärmen står kvar och fungerar. Det
  som väntar på servern markeras som väntande, resten går som vanligt.
- **Operatören ser skillnad på bekräftat och på väg.** Ett skickat kommando
  syns som obekräftat tills servern svarat.
- **Fel går att förstå.** Boxen kan säga att servern är tyst utan att kasta
  bort sin bild av världen.

## 2. Hur det känns

Alla skärmar är 16×2. A är gör, C är nästa, `*` är tillbaka, `#` är sök.

**Startskärm — stationsöversikt**

```
2134 SPAR 2
HBG/MLM 06:14
```

C bläddrar till nästa tågrörelse. Omedelbart, ingen väntan.
`*` öppnar språkmenyn. A går in på rörelsen.

**Tågrörelse**

```
2134 AVVAKTAR
A=BEGAR TILLST
```

A startar klareringen. Eftersom en klarering måste namnge vilken linje tåget
tar öppnas grannvalet först.

**Grannval**

```
MOT MALMO
C=NASTA A=VALJ
```

A skickar. Nu, och först nu, går något över nätet.

**Väntar på svar**

```
2134 SKICKAT
VANTAR PA SVAR
```

Här är skillnaden mot idag: skärmen är ritad direkt vid tryck, innan paketet
lämnat lådan. Kommer svaret inom en sekund märker operatören ingen väntan alls.

**Svar från servern**

```
2134 KLARERAD
MOT MALMO 06:14
```

eller

```
2134 NEKAD
SPAR UPPTAGET
```

**När servern är tyst** — och det här är hela poängen:

```
2134 SKICKAT
SERVERN SVARAR EJ
```

Boxen behåller sin bild. Du kan bläddra, titta, slå upp tåg. Bara det som
kräver serverns godkännande står och väntar. När servern kommer tillbaka
avgörs det väntande och skärmen uppdateras.

## 3. Arkitektur

### Vem äger vad

**Boxen äger** navigationen, skärmarna, inmatningen, språkvalet, och vad som
visas när. All logik kompileras in. Ingen konfigurationsfil vandrar över nätet
— vi uppdaterar firmware när funktionerna ändras.

**Servern äger** sanningen om trafiken, vem som får göra vad, och hur konflikter
avgörs. Servern prövar varje kommando mot sina egna regler, oavsett vad boxen
tror. En box med gammal firmware kan aldrig få igenom något otillåtet.

Gränsen i en mening: **boxen bestämmer vad som erbjuds, servern bestämmer vad
som beviljas.**

Det enda som fortsätter komma som data från servern är `allowed_actions` per
tågrörelse, precis som idag. Det är tillstånd, inte program.

### Tre slags trafik

| Riktning | Vad | När |
|---|---|---|
| Box → server → box | Kommando med korrelations-id, svar tillbaka | Vid tryck på A |
| Server → box | Modelluppdatering som berör just den boxen | När trafikläget ändras |
| Server → box | Knackning: någon begär något av dig | När en granne begär klarering |

Ingen periodisk hämtning. Inga pixlar. Boxen prenumererar på sin stations
modell, inte på en färdigmålad skärm.

### Uppdateringar

Firmware via OTA, orkestrerad av servern. Servern vet redan vilken version varje
box kör — den tas emot i `hello` och sparas. Utrullning en station i taget.

## 4. Teknisk lösning

### Vad som redan finns

`firmware/esp32/lib/tmbox_core` innehåller modellen, navigationen, renderaren,
uppmärksamhetslogiken och språkmenyn, med en testsvit som körs på vanlig dator
utan hårdvara. Det är i allt väsentligt den lokala app den här planen beskriver.

Fältets 16×2-boxar (ESP8266) kör inte den. De kör `firmware/common/server_terminal.h`,
där servern skickar färdiga rader och boxen ritar dem.

**Arbetet är alltså inte att skriva appen. Det är att flytta ESP8266 till den
som finns, och byta ut pixelströmmen mot en modellström.**

### Vad som byggs

**Server**
- Publicera stationens modell per box i stället för renderad `frame`.
- Ta emot kommando med korrelations-id, svara på samma id.
- Behålla `allowed_actions` per rörelse.
- Loggning enligt steg 0.

**Firmware**
- ESP8266 använder `tmbox_core` i stället för `server_terminal.h`.
- Optimistisk ritning: rita vid tryck, markera obekräftat, stäm av vid svar.
- Skilj "har kontakt med servern" från "har en modell att visa". En tyst server
  tömmer inte skärmen.
- Kom ihåg värd och port mellan anslutningar. mDNS bara när det kända paret
  inte svarar. Det gör återhämtningen till sekunder i stället för minuter.

### Vad som tas bort

- Serverns rendering av `frame` för 16×2.
- Jämförelsen som avgör om en frame ändrats, och lagringen av förra framen per box.
- Femtonsekunderstålamodet som villkor för att visa något alls.

### Övergång

Servern talar båda protokollen under övergången. En box som kör gammal firmware
får frames som idag; en box som kör ny får modellen. `protocol_version` i
`hello` avgör, precis som redan sker mellan v1 och v2.

## 5. Kostnad och risk

**ESP8266:ans minne.** Omkring 40 KB fri heap, och modellen ska rymmas
tillsammans med JSON-tolkningen. Det ska mätas på en riktig station med full
trafik innan vi låser formatet, inte antas.

**Två protokoll samtidigt.** Under övergången finns båda vägarna, och båda ska
testas. Det är hanterbart men det är arbete.

**Fältuppdatering.** Alla boxar måste flashas. OTA ska fungera innan vi behöver
det, inte utvecklas när vi står i det.

**Det här löser inte kvällens fel av sig självt.** Om servern inte svarar alls
kan en lokal app fortfarande inte skicka klareringar. Den gör avbrottet
synligt och uthärdligt i stället för totalt — och steg 0 gör att vi ser
orsaken nästa gång det händer.

## 6. Beslut jag behöver

**1. Ska steg 0 göras först och släppas separat?**
Rekommendation: ja. Det är litet, rör bara servern, och ger oss ögon på felet
medan resten byggs.

**2. Vad får en box göra när servern är tyst?**
Alternativ: (a) bara titta och bläddra, allt som ändrar något spärras;
(b) kön byggs lokalt och skickas när servern kommer tillbaka.
Rekommendation: (a). En klarering som ligger i kö i en låda och verkställs två
minuter senare är farligare än att inte kunna klarera alls.

**3. Ska ESP32 och ESP8266 gå över samtidigt?**
Rekommendation: nej. ESP32 först — den har minnet och kör redan `tmbox_core` —
sedan ESP8266 när formatet är mätt och stabilt.

**4. Hur snabbt ska ett svar komma för att kännas omedelbart?**
Rekommendation: 200 ms från tryck till bekräftat på skärmen, mätt i fält.
Den lokala ritningen sker på under 10 ms oavsett; siffran gäller serversvaret.

**5. Ska OTA byggas i den här omgången eller före?**
Rekommendation: före. Vi kommer behöva rulla ut firmware flera gånger under
övergången, och att göra det manuellt på plats är inte rimligt.

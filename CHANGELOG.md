# Ändringar i TrainMeet Server

Versionsnumret sätts automatiskt vid merge till main. Se
[docs/VERSIONING.md](docs/VERSIONING.md) för hur nivån bestäms.

## 1.3.2

Serveradministrationen flyttar ur byggflödet.

### Inställningar som eget läge

För att uppdatera programvaran fick en operatör trycka **Bygg om träffen** —
ett flöde om träffens innehåll, som varnar att inget slår igenom förrän man
aktiverar. Kugghjulet på översikten hette dessutom "Öppna administration" men
landade i steg 1, som svarar på var träffen kommer ifrån.

Att köra, att bygga och att administrera är tre olika saker:

| Läge | Vad |
|---|---|
| KÖR | Det som händer under träffen |
| BYGG | Träffens innehåll: källa, bana, tidtabell, TMBoxar |
| **Inställningar** | Servern själv: identitet, åtkomst, programvara, Cloud-koppling, nollställning |

Nås med kugghjulet i topplocket. Programuppdateringen är ett klick från
översikten i stället för fyra. Innehållet är oflyttat i sak — samma sektioner,
samma API-anrop, samma sju uppdateringssteg.

BYGG har därför fyra steg, där designpaketet lägger serverinställningar som
steg 5. Avvikelse 7 i `docs/DESIGNPAKET-DOD.md`.

### Mindre

- `currentMode()` svarade `kor` för allt utom `bygg`. Den hade inga anropare,
  men samma tvåvägsval satt på startraden: ett sparat inställningsläge kunde
  aldrig läsas tillbaka, så en omladdning landade i KÖR.
- Flikraden hade `overflow-x: auto` men saknade `min-width: 0`, så den knuffade
  i stället för att scrollas vid smala fönster.

---

## 1.3.1

Layoutfel som nådde produktion, och en version som säger vilken.

### KÖR fick hela fönstret

Hela gränssnittet ritades i vänstra fjärdedelen av ett brett fönster. Skalet
bar kvar tvåkolumnsgridden från de tolv menypunkterna: byggsidofältet är dolt i
KÖR, men **grid-spåret fanns kvar**, så arbetsytan hamnade i det och mätte
250 px mot ett 1392 px skal.

Båda lägena sätter numera sin egen layout. Arbetsytan hade dessutom två regler
med samma selektor, en för visning och en för bredd; de är sammanslagna,
eftersom två ställen som båda sätter bredd glider isär.

### Ett formulär som krävde mer plats än som fanns

Vid 924 px svämmade formuläret för extern admininloggning över sitt steg med
26 px. `repeat(3, minmax(180px, 1fr))` kräver 598 px, och ett byggsteg vid
924 px fönster har 572 px — sidofältet tar resten. Medieförfrågan som skulle
fällt ihop det lyssnar på fönstret, inte på ytan, så den slog aldrig till.
`auto-fit` räknar på den plats som faktiskt finns.

### Mindre

- Kryssrutor och radioknappar fick den globala fältregelns fulla bredd och
  14 px radie, och renderades som stora rundade fyrkanter.
- Stationsväljaren i KÖR › Trafik sträckte sig över hela fönstret.
- *Öppna TKL* bar webbläsarens länkunderstrykning bland knappar.

---

## 1.3.0

Adminen byggd om mot designpaketet. Tolv menypunkter blir två lägen.

### KÖR och BYGG

Fem körflikar och fem byggsteg i stället för tolv menypunkter. `mode` på
`body` är roten: enda variabeln som byter hela skelettet. Byggläget har eget
mörkt topplock, numrerad stegräcka och en panel för osparade ändringar.

BYGG steg 1 gör källvalet till serverns driftläge i stället för en etikett.
Steg 2 visar stationer, sträckor och A–D-paneler ur ett enda API, med samma
form vare sig innehållet kommer från Cloud eller ett lokalt utkast. Steg 5
samlar de tre systemmenypunkterna.

### Ingen tyst Cloud-aktivering

Pollern hämtade var femtonde sekund och anropade `install()`, vars `activate`
är `True` som standard, och sedan `request_restart()`. En operatör som rättat
tre avgångstider kl 13 förlorade dem när Cloud publicerade kl 14 — och träffen
startade om medan det hände.

Nu hämtar den, lagrar, markerar som väntande och slutar. `/v1/runtime/pending`
svarar med en **diff**, inte en räkning, och aktivering kräver att revisionens
id skickas tillbaka.

### Tidtabellen går att rätta i Cloud-läge

Den gamla grinden stängde varje skrivväg medan Cloud var redaktör, vilket
gjorde en rättad avgångstid omöjlig av samma skäl som en omritad bana. Under
träffen *är* servern driften. Grinden gäller numera bara stationer, sträckor
och paneler.

### Mindre

- Typsnitten serveras från servern i stället för från Google.
- Ett CSP-fel som legat på `main` är lagat: stapelbredder sattes med
  `style`-attribut, som serverns egen `style-src 'self'` avvisar.

---

## 1.2.0

Den första versionen där servern kan rätta en träff som redan är igång.

### Servern redigerar det Cloud publicerat (D2)

Servern kunde alltid bygga en egen konfiguration. Vad den **inte** kunde var
att redigera den Cloud publicerat — vilket är det enda som är värt att rätta
under en träff, när problemet är en tid i en tidtabell som gick ut för en
timme sedan.

Ett hämtat paket går nu att öppna som arbetskopia. Aktivering skriver en ny
paketrevision `<bas>+local-rN` genom samma maskineri som en Cloud-publicering,
så TKL och boxarna ser en `config_version` de inte sett och läser om. Rutter
och tjänststopp härleds ur tågraderna, så en rättad tid kan inte säga emot
tidtabellen som visar den.

Särfallet «topologi utan trafik» är borta: ett lokalt paket byggdes förut med
`trains: []`, alltså en järnväg utan tåg på.

### Driftlägen, och synlig kastning (D3, D4)

`cloud-linked` betyder att Cloud är redaktör och servern vägrar lokala
ändringar. `offline-meet` öppnar dem. Läget är beständigt tillstånd som en
människa satt och härleds **aldrig** ur om Cloud svarade just nu — ett
nätavbrott låser inte upp redigering, och ett nät som återkommer låser inte
mitt i någons arbete.

Att gå tillbaka till Cloud kastar de lokala revisionerna, och aldrig tyst:
servern visar först exakt vilka rader som ändrats, lagts till eller tagits
bort, och kräver bekräftelse.

### Uppdateringsförloppet

Sju verkliga steg i stället för tre, med **hälsokontroll före klart**. Förut
skrevs `complete` innan omstarten, så en administratör fick veta att
uppdateringen lyckats innan den provats en enda gång. `GET /healthz` är ny.

### Enkelriktat flöde mot Cloud (D1)

Förslagskön är borttagen ur båda repona. Cloud publicerar, servern hämtar,
ingenting går tillbaka.

### Spårbeläggning (D5)

`track_occupied` stod i protokollets lista över avslag utan att kunna
inträffa. Nu kan det: ett spår som redan är tilldelat en icke-avgången
rörelse går inte att tilldela igen, oavsett väg in.

### Mindre

- Inloggningsfältet är tomt. Applikationen fyller inte i användarnamnet, och
  `/v1/auth/status` lämnar det inte längre till oautentiserade anrop.
- Typsnitten serveras lokalt i stället för från Google Fonts, som serverns
  egen CSP avvisade vid varje sidladdning.
- Versionsnumret är riktigt, inte en git-sha. Commit-id finns kvar som
  bygginformation under **Teknisk information**.

### Kompatibilitet

| Kontrakt | Version |
|---|---|
| TMBox-protokoll | `protocol_version: 2` |
| Driftpaket från Cloud | `schema_version: 3` |
| TrainMeet Cloud | 1.0.0 eller senare |

Befintlig träffdata bevaras. Utkast sparade före 1.2.0 migreras vid läsning.

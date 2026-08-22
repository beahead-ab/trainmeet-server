# Ändringar i TrainMeet Server

Versionsnumret sätts automatiskt vid merge till main. Se
[docs/VERSIONING.md](docs/VERSIONING.md) för hur nivån bestäms.

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

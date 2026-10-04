/* Shared EU/US selection copy; UI language never changes operating rules. */
(() => {
  const rows = [
  {
    "en": "Operating session type",
    "sv": "Typ av tågträff",
    "da": "Type af togtræf",
    "nb": "Type togtreff",
    "de": "Betriebsart"
  },
  {
    "en": "US operating session",
    "sv": "US-tågträff",
    "da": "US-togtræf",
    "nb": "US-togtreff",
    "de": "US-Betriebstreffen"
  },
  {
    "en": "EU operating session",
    "sv": "EU-tågträff",
    "da": "EU-togtræf",
    "nb": "EU-togtreff",
    "de": "EU-Betriebstreffen"
  },
  {
    "en": "TKL · station operations · TMBox",
    "sv": "TKL · stationsdrift · TMBox",
    "da": "TKL · stationsdrift · TMBox",
    "nb": "TKL · stasjonsdrift · TMBox",
    "de": "TKL · Bahnhofsbetrieb · TMBox"
  },
  {
    "en": "Dispatcher · Conductor · Track Warrant Control",
    "sv": "Dispatcher · Conductor · Track Warrant Control",
    "da": "Dispatcher · Conductor · Track Warrant Control",
    "nb": "Dispatcher · Conductor · Track Warrant Control",
    "de": "Dispatcher · Conductor · Track Warrant Control"
  },
  {
    "en": "UI language is a separate choice. US operating views default to English.",
    "sv": "Språk väljs separat. US-driftvyer börjar på engelska.",
    "da": "Sprog vælges separat. US-driftsvisninger starter på engelsk.",
    "nb": "Språk velges separat. US-driftsvisninger starter på engelsk.",
    "de": "Die Sprache wird separat gewählt. US-Betriebsansichten starten auf Englisch."
  },
  {
    "en": "The session type is locked once content has been added. Create a new session to use the other type.",
    "sv": "Träfftypen är låst när innehåll har lagts till. Skapa en ny träff för den andra typen.",
    "da": "Træftypen låses, når indhold er tilføjet. Opret et nyt træf for den anden type.",
    "nb": "Trefftypen låses når innhold er lagt til. Opprett et nytt treff for den andre typen.",
    "de": "Nach dem Hinzufügen von Inhalten ist die Betriebsart gesperrt. Erstellen Sie für die andere Betriebsart ein neues Treffen."
  },
  {
    "en": "US Cloud import and publishing are not available yet. Open US Dispatcher on your local TrainMeet Server to load a US package.",
    "sv": "Cloud-import och publicering för US är inte tillgängliga ännu. Öppna US Dispatcher på din lokala TrainMeet Server för att läsa in ett US-paket.",
    "da": "Cloud-import og publicering for US er endnu ikke tilgængelige. Åbn US Dispatcher på din lokale TrainMeet Server for at indlæse en US-pakke.",
    "nb": "Cloud-import og publisering for US er ikke tilgjengelig ennå. Åpne US Dispatcher på din lokale TrainMeet Server for å laste inn en US-pakke.",
    "de": "Cloud-Import und Veröffentlichung für US sind noch nicht verfügbar. Öffnen Sie US Dispatcher auf Ihrem lokalen TrainMeet Server, um ein US-Paket zu laden."
  },
  {
    "en": "Choose EU or US when creating the session.",
    "sv": "Välj EU eller US när du skapar träffen.",
    "da": "Vælg EU eller US, når du opretter træffet.",
    "nb": "Velg EU eller US når du oppretter treffet.",
    "de": "Wählen Sie EU oder US beim Erstellen des Treffens."
  },
  {
    "en": "Switching views does not change or stop an operating session.",
    "sv": "Att byta vy ändrar eller stoppar inte en körning.",
    "da": "Skift af visning ændrer eller stopper ikke en kørsel.",
    "nb": "Bytte av visning endrer eller stopper ikke en kjøring.",
    "de": "Ein Ansichtswechsel ändert oder stoppt keine Betriebssitzung."
  },
  {
    "en": "Country",
    "sv": "Land",
    "da": "Land",
    "nb": "Land",
    "de": "Land"
  },
  {
    "en": "Sweden",
    "sv": "Sverige",
    "da": "Sverige",
    "nb": "Sverige",
    "de": "Schweden"
  },
  {
    "en": "Denmark",
    "sv": "Danmark",
    "da": "Danmark",
    "nb": "Danmark",
    "de": "Dänemark"
  },
  {
    "en": "Germany",
    "sv": "Tyskland",
    "da": "Tyskland",
    "nb": "Tyskland",
    "de": "Deutschland"
  },
  {
    "en": "Norway",
    "sv": "Norge",
    "da": "Norge",
    "nb": "Norge",
    "de": "Norwegen"
  },
  {
    "en": "USA",
    "sv": "USA",
    "da": "USA",
    "nb": "USA",
    "de": "USA"
  },
  {
    "en": "Operating session in {country}",
    "sv": "Tågträff i {country}",
    "da": "Togtræf i {country}",
    "nb": "Togtreff i {country}",
    "de": "Betriebstreffen in {country}"
  },
  {
    "en": "Swedish · left-hand traffic",
    "sv": "Svenska · vänstertrafik",
    "da": "Svensk · venstrekørsel",
    "nb": "Svensk · venstretrafikk",
    "de": "Schwedisch · Linksverkehr"
  },
  {
    "en": "Danish · right-hand traffic",
    "sv": "Danska · högertrafik",
    "da": "Dansk · højrekørsel",
    "nb": "Dansk · høyretrafikk",
    "de": "Dänisch · Rechtsverkehr"
  },
  {
    "en": "German · right-hand traffic",
    "sv": "Tyska · högertrafik",
    "da": "Tysk · højrekørsel",
    "nb": "Tysk · høyretrafikk",
    "de": "Deutsch · Rechtsverkehr"
  },
  {
    "en": "Norwegian · left-hand traffic",
    "sv": "Norska · vänstertrafik",
    "da": "Norsk · venstrekørsel",
    "nb": "Norsk · venstretrafikk",
    "de": "Norwegisch · Linksverkehr"
  },
  {
    "en": "English · Track Warrant Control",
    "sv": "Engelska · Track Warrant Control",
    "da": "Engelsk · Track Warrant Control",
    "nb": "Engelsk · Track Warrant Control",
    "de": "Englisch · Track Warrant Control"
  },
  {
    "en": "Sweden, Denmark, Germany and Norway run the same traffic and can be switched at any time. The USA runs the separate US flow.",
    "sv": "Sverige, Danmark, Tyskland och Norge kör samma trafikspel och kan bytas när som helst. USA kör det separata US-flödet.",
    "da": "Sverige, Danmark, Tyskland og Norge kører samme trafikspil og kan skiftes når som helst. USA kører det separate US-flow.",
    "nb": "Sverige, Danmark, Tyskland og Norge kjører samme trafikkspill og kan byttes når som helst. USA kjører den separate US-flyten.",
    "de": "Schweden, Dänemark, Deutschland und Norwegen nutzen denselben Betrieb und können jederzeit gewechselt werden. Die USA nutzen den separaten US-Ablauf."
  },
  {
    "en": "Switching to or from the USA is only possible on an empty meet.",
    "sv": "Byte till eller från USA går bara på en tom träff.",
    "da": "Skift til eller fra USA er kun muligt på et tomt træf.",
    "nb": "Bytte til eller fra USA går bare på et tomt treff.",
    "de": "Ein Wechsel zu oder von den USA ist nur bei einem leeren Treffen möglich."
  },
  {
    "en": "Choose the country when creating the meet.",
    "sv": "Välj land när du skapar träffen.",
    "da": "Vælg land, når du opretter træffet.",
    "nb": "Velg land når du oppretter treffet.",
    "de": "Wählen Sie das Land beim Erstellen des Treffens."
  }
];
  globalThis.TrainMeetMessages ||= {};
  for (const row of rows) for (const source of Object.values(row)) globalThis.TrainMeetMessages[source] = row;
})();


"""16x2 copy: translated before interpolation, never inside station identities."""
from .device_ui import LANGUAGES, text as legacy_text

# Swedish | English | Danish | Norwegian | German. LCD hints <= 11 cells; the
# state words on row 3 of a 20x4 box <= 13, beside the track.
COPY = """Nr# A:Kö|No# A:Q|Nr# A:Kø|Nr# A:Kø|Nr# A:Q
B:Akt{count} C/D|B:Act{count} C/D|B:Akt{count} C/D|B:Akt{count} C/D|B:Akt{count} C/D
A:K{count} B:Akt|A:Q{count} B:Act|A:K{count} B:Akt|A:K{count} B:Akt|A:Q{count} B:Akt
#Avg C/D|#Dep C/D|#Afg C/D|#Avg C/D|#Ab C/D
C/D B:Öv|C/D B:Home|C/D B:Over|C/D B:Over|C/D B:Alle
#Avg|#Dep|#Afg|#Avg|#Ab
#In|#In|#Ind|#Inn|#An
INGA AKTIVA TÅG|NO ACTIVE TRAINS|INGEN AKTIVE TOG|INGEN AKTIVE TOG|KEINE AKTIVEN
Visa aktiva tåg|Show active trains|Vis aktive tog|Vis aktive tog|Aktive Züge anzeigen
Aktiva tåg ({count})|Active trains ({count})|Aktive tog ({count})|Aktive tog ({count})|Aktive Züge ({count})
Föregående aktiva tåg|Previous active train|Forrige aktive tog|Forrige aktive tog|Vorheriger aktiver Zug
Nästa aktiva tåg|Next active train|Næste aktive tog|Neste aktive tog|Nächster aktiver Zug
Aktivt tåg {position}/{count}|Active train {position}/{count}|Aktivt tog {position}/{count}|Aktivt tog {position}/{count}|Aktiver Zug {position}/{count}
Ingen vald aktiv rörelse. C/D väljer; B visar översikten.|No active movement selected. C/D selects; B shows overview.|Ingen aktiv bevægelse valgt. C/D vælger; B viser oversigt.|Ingen aktiv bevegelse valgt. C/D velger; B viser oversikt.|Keine aktive Fahrt gewählt. C/D wählt; B zeigt die Übersicht.
A:Kö #Visa|A:Q #Show|A:Kø #Vis|A:Kø #Vis|A:Q #Zeig
#OK *=Bak|#OK *=Back|#OK *=Ret|#OK *=Ret|#OK *=Zur
#Ja *Nej|#Yes *No|#Ja *Nej|#Ja *Nei|#Ja *Nein
#Ja {count}|#Yes {count}|#Ja {count}|#Ja {count}|#Ja {count}
A:Kö *=Bak|A:Q *=Back|A:Kø *=Ret|A:Kø *=Ret|A:Q *=Zur
#Välj A:Kö|#Pick A:Q|#Vælg A:Kø|#Velg A:Kø|#Wahl A:Q
C/D A:Kö|C/D A:Q|C/D A:Kø|C/D A:Kø|C/D A:Q
B:Fil A:Kö|B:Fil A:Q|B:Fil A:Kø|B:Fil A:Kø|B:Fil A:Q
*=Bak|*=Back|*=Ret|*=Ret|*=Zur
#In C/D:Sp|#In C/D:Tr|#Ind C/D:Sp|#Inn C/D:Sp|#An C/D:Gl
#Beg A:Kö|#Req A:Q|#Bed A:Kø|#Be A:Kø|#Anfr A:Q
#Avg *Åter|#Dep *Undo|#Afg *Ret|#Avg *Ret|#Ab *Zur
#In B:Sp|#In B:Tr|#Ind B:Sp|#Inn B:Sp|#An B:Gl
*Åter B:Öv|*Undo B:All|*Ret B:Vis|*Ret B:Vis|*Zur B:Alle
#Sändklar|#Reserve|#Reservér|#Reserver|#Reserv.
INGA FRÅGOR|NO REQUESTS|INGEN SPØRGSMÅL|INGEN FORESP.|KEINE ANFRAGEN
FRÅGAN ÄNDRAD|REQUEST CHANGED|FORESP. ÆNDRET|FORESP. ENDRET|ANFRAGE GEÄNDERT
VÄLJ NÄSTA TÅG|PICK NEXT TRAIN|VÆLG NÆSTE TOG|VELG NESTE TOG|NÄCHSTEN ZUG
INGA TÅG|NO TRAINS|INGEN TOG|INGEN TOG|KEINE ZÜGE
INGA ANKOMSTER|NO ARRIVALS|INGEN ANKOMSTER|INGEN ANKOMSTER|KEINE ANKÜNFTE
INGA AVGÅNGAR|NO DEPARTURES|INGEN AFGANGE|INGEN AVGANGER|KEINE ABFAHRTEN
LÄGET ÄNDRAT|STATE CHANGED|STATUS ÆNDRET|STATUS ENDRET|STATUS GEÄNDERT
ÅTER {number}?|UNDO {number}?|FORTRYD {number}?|ANGRE {number}?|ZURÜCK {number}?
NEKA {number}?|REFUSE {number}?|AFVIS {number}?|AVVIS {number}?|NEIN {number}?
{number} SPÅR {track}|{number} TRK {track}|{number} SPOR {track}|{number} SPOR {track}|{number} GL {track}
MOTTAGET|ARRIVED|ANKOMMET|ANKOMMET|ANGEKOMMEN
ÅTERTAGET|WITHDRAWN|TRUKKET|TRUKKET|ZURÜCK
NEKAT|REFUSED|AFVIST|AVVIST|ABGELEHNT
INGET TÅG|NO TRAIN|INTET TOG|INGEN TOG|KEIN ZUG
ANNAN SIDA|OTHER SIDE|ANDEN SIDE|ANNEN SIDE|ANDERE SEITE
FLERA TÅG ADMIN|MULTIPLE - ADMIN|FLERE - ADMIN|FLERE - ADMIN|MEHRERE - ADMIN
UPPT SPÅR|TRK BUSY|SPOR OPT|SPOR OPPT|GLEIS BES
Placera på spår|Place on track|Placér på spor|Plasser på spor|Auf Gleis stellen
Placera på spår…|Place on track…|Placér på spor…|Plasser på spor…|Auf Gleis stellen…
FLYTTA {number} HIT?|MOVE {number} HERE?|FLYT {number} HERTIL?|FLYTT {number} HIT?|ZUG {number} HOLEN?
FLYTTA {number}?|MOVE {number}?|FLYT {number}?|FLYTT {number}?|HOLEN {number}?
#Ja B:Sp|#Yes B:Trk|#Ja B:Sp|#Ja B:Sp|#Ja B:Gl
Flytta hit|Move here|Flyt hertil|Flytt hit|Hierher holen
TÅG: _____|NO.: _____|TOG: _____|TOG: _____|ZUG: _____
#Sök B:Del|#Find B:Del|#Søg B:Del|#Søk B:Del|#Such B:Del
VÄNTAR PÅ ADMIN|WAITING ADMIN|VENTER PÅ ADMIN|VENTER PÅ ADMIN|WARTE AUF ADMIN
ANSLUTER|CONNECTING|FORBINDER|KOBLER TIL|VERBINDE
Förfrågningskö ({count} väntar)|Request queue ({count} waiting)|Forespørgsler ({count} venter)|Forespørsler ({count} venter)|Anfragen ({count} warten)
Stäng meddelande|Close message|Luk besked|Lukk melding|Meldung schließen
Tillbaka|Back|Tilbage|Tilbake|Zurück
Visa väntande förfrågningar|Show pending requests|Vis forespørgsler|Vis forespørsler|Anfragen anzeigen
Visa kommande tåg|Show upcoming trains|Vis kommende tog|Vis kommende tog|Kommende Züge
Föregående tåg|Previous train|Forrige tog|Forrige tog|Vorheriger Zug
Nästa tåg|Next train|Næste tog|Neste tog|Nächster Zug
B:Akt{count}|B:Act{count}|B:Akt{count}|B:Akt{count}|B:Akt{count}
A{count} B:Akt|A{count} B:Act|A{count} B:Akt|A{count} B:Akt|A{count} B:Akt
Översikt utan trafikändring|Overview without traffic change|Oversigt uden trafikændring|Oversikt uten trafikkendring|Übersicht ohne Änderung
Föregående förfrågan|Previous request|Forrige forespørgsel|Forrige forespørsel|Vorherige Anfrage
Nästa förfrågan|Next request|Næste forespørgsel|Neste forespørsel|Nächste Anfrage
Ge klart|Approve request|Giv klar|Gi klart|Zustimmen
Neka begäran…|Refuse request…|Afvis forespørgsel…|Avvis forespørsel…|Anfrage ablehnen…
Visa ankomster|Show arrivals|Vis ankomster|Vis ankomster|Ankünfte anzeigen
Visa avgångar|Show departures|Vis afgange|Vis avganger|Abfahrten anzeigen
Visa alla tåg|Show all trains|Vis alle tog|Vis alle tog|Alle Züge anzeigen
Välj tåg|Select train|Vælg tog|Velg tog|Zug wählen
Behåll begäran eller klartecken|Keep request or approval|Behold forespørgsel|Behold forespørsel|Anfrage beibehalten
Bekräfta återtagning|Confirm withdrawal|Bekræft tilbagetrækning|Bekreft tilbaketrekking|Rücknahme bestätigen
Tillbaka utan att neka|Back without refusing|Tilbage uden at afvise|Tilbake uten å avvise|Zurück ohne Ablehnung
Bekräfta neka|Confirm refusal|Bekræft afvisning|Bekreft avvisning|Ablehnung bestätigen
Ankommit på valt spår|Arrived on selected track|Ankommet på valgt spor|Ankommet på valgt spor|Auf gewähltem Gleis angekommen
Föregående spår|Previous track|Forrige spor|Forrige spor|Vorheriges Gleis
Nästa spår|Next track|Næste spor|Neste spor|Nächstes Gleis
Reservera|Reserve|Reservér|Reserver|Reservieren
Begär klartecken|Request clearance|Anmod om klar|Be om klart|Erlaubnis anfragen
Återta begäran…|Withdraw request…|Træk forespørgsel tilbage…|Trekk forespørsel tilbake…|Anfrage zurücknehmen…
Rapportera avgång|Report departure|Meld afgang|Meld avgang|Abfahrt melden
Återta klartecken…|Withdraw clearance…|Træk klar tilbage…|Trekk klart tilbake…|Erlaubnis zurücknehmen…
Rapportera ankomst|Report arrival|Meld ankomst|Meld ankomst|Ankunft melden
Annat ankomstspår|Different arrival track|Andet ankomstspor|Annet ankomstspor|Anderes Ankunftsgleis
Sök tåg|Find train|Søg tog|Søk tog|Zug suchen
Sök tåg (begär direkt)|Find train (asks at once)|Søg tog (anmoder straks)|Søk tog (spør straks)|Zug suchen (fragt sofort an)
Avbryt inmatning|Cancel input|Annuller indtastning|Avbryt inntasting|Eingabe abbrechen
Sudda siffra|Erase digit|Slet ciffer|Slett siffer|Ziffer löschen
Förfrågningskö (avbryt inmatning)|Requests (cancel input)|Forespørgsler (annuller input)|Forespørsler (avbryt input)|Anfragen (Eingabe abbrechen)
Skriv tågnummer direkt, eller bläddra med C/D|Type a train number, or browse with C/D|Skriv tognummer, eller blad med C/D|Skriv tognummer, eller bla med C/D|Zugnummer eingeben oder mit C/D blättern
Tåg {number} · {direction} {station} · planerat {time}|Train {number} · {direction} {station} · scheduled {time}|Tog {number} · {direction} {station} · planlagt {time}|Tog {number} · {direction} {station} · planlagt {time}|Zug {number} · {direction} {station} · geplant {time}
Avgång till|Departure to|Afgang til|Avgang til|Abfahrt nach
Ankomst från|Arrival from|Ankomst fra|Ankomst fra|Ankunft von
alla tåg|all trains|alle tog|alle tog|alle Züge
ankomster|arrivals|ankomster|ankomster|Ankünfte
avgångar|departures|afgange|avganger|Abfahrten
Förfrågan {position}/{count} · |Request {position}/{count} · |Forespørgsel {position}/{count} · |Forespørsel {position}/{count} · |Anfrage {position}/{count} · 
Ingen vald förfrågan. A öppnar kön; B visar översikten.|No request selected. A opens requests; B shows overview.|Ingen valgt forespørgsel. A åbner køen; B viser oversigt.|Ingen valgt forespørsel. A åpner køen; B viser oversikt.|Keine Anfrage gewählt. A öffnet Anfragen; B die Übersicht.
STÄNG|CLOSE|LUK|LUKK|SCHLIESSEN
TILLBAKA|BACK|TILBAGE|TILBAKE|ZURÜCK
OK|OK|OK|OK|OK
VISA KÖ|SHOW QUEUE|VIS KØ|VIS KØ|ANFRAGEN
KOMMANDE|UPCOMING|KOMMENDE|KOMMENDE|KOMMENDE
FÖREG|PREV|FORRIGE|FORRIGE|VORHER
NÄSTA|NEXT|NÆSTE|NESTE|WEITER
ÖVERSIKT|OVERVIEW|OVERSIGT|OVERSIKT|ÜBERSICHT
GE KLART|APPROVE|GIV KLAR|GI KLART|FREIGEBEN
NEKA|REFUSE|AFVIS|AVVIS|ABLEHNEN
FILTER|FILTER|FILTER|FILTER|FILTER
VÄLJ|SELECT|VÆLG|VELG|WÄHLEN
BEHÅLL|KEEP|BEHOLD|BEHOLD|BEHALTEN
ÅTERTA|WITHDRAW|TRÆK TILB.|TREKK TILB.|ZURÜCKNEHMEN
INNE|ARRIVED|ANKOMMET|ANKOMMET|ANGEKOMMEN
RESERVERA|RESERVE|RESERVÉR|RESERVER|RESERVIEREN
BEGÄR|REQUEST|ANMOD|BE OM|ANFRAGEN
AVGÅTT|DEPARTED|AFGÅET|AVGÅTT|ABGEFAHREN
SPÅR|TRACK|SPOR|SPOR|GLEIS
PLACERA|PLACE|PLACÉR|PLASSER|STELLEN
FLYTTA|MOVE|FLYT|FLYTT|HOLEN
KÖ|QUEUE|KØ|KØ|ANFRAGEN
AKTIVA|ACTIVE|AKTIVE|AKTIVE|AKTIV
SÖK|FIND|SØG|SØK|SUCHEN
AVBRYT|CANCEL|ANNULLER|AVBRYT|ABBRECHEN
SUDDA|ERASE|SLET|SLETT|LÖSCHEN
SIMULERING|SIMULATION|SIMULERING|SIMULERING|SIMULATION
Till|To|Til|Til|Nach
Från|From|Fra|Fra|Von
Sp {track}|Trk {track}|Sp {track}|Sp {track}|Gl {track}
Ej begärd|Not asked|Ej anmodet|Ikke bedt om|Ungefragt
Ej skickad|Not sent|Ej sendt|Ikke sendt|Ungesendet
Väntar svar|Awaiting OK|Venter svar|Venter svar|Wartet Antw.
Vill skicka|Asks to send|Vil sende|Vil sende|Will senden
Klar att avgå|Clear to go|Kan afgå|Kan avgå|Fahrt frei
Klarerad|Cleared|Klareret|Klarert|Freigegeben
Avgått|Departed|Afgået|Avgått|Abgefahren
På väg hit|On its way|På vej|På vei hit|Unterwegs
Administratören tilldelar station i Inställningar.|The administrator assigns the station in Settings.|Administratoren tildeler stationen under Indstillinger.|Administratoren tildeler stasjonen under Innstillinger.|Der Administrator weist den Bahnhof in den Einstellungen zu.
Tåg {number} mottaget i {station}. Meddelandet försvinner automatiskt.|Train {number} arrived at {station}. This message closes automatically.|Tog {number} ankommet til {station}. Beskeden lukkes automatisk.|Tog {number} ankommet i {station}. Meldingen lukkes automatisk.|Zug {number} in {station} angekommen. Meldung schließt automatisch.
"""

MESSAGES = {code: {} for code, _ in LANGUAGES}
for line in COPY.splitlines():
    values = line.split("|")
    assert len(values) == 5, line
    for code, value in zip(("sv", "en", "da", "nb", "de"), values):
        MESSAGES[code][values[0]] = value

def text(language, key, **values):
    return MESSAGES.get(language, MESSAGES["sv"]).get(key, legacy_text(language, key)).format(**values)

def notice(language, value):
    # Notice is a structured number + catalogue token, not a rendered frame.
    number, _, suffix = value.partition(" ")
    if number.isdigit() and suffix in {"MOTTAGET", "ÅTERTAGET", "NEKAT", "UPPT SPÅR"}:
        return number + " " + text(language, suffix)
    return text(language, value)

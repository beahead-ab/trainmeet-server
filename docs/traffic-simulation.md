# Trafikspelssimulatorn är borttagen

Simuleringsläget (Drift → Simulering, `/v1/simulation`) togs bort i Server 4.0
(Casper 2026-10-10). Det körde träffens tidtabell i en egen databas, med
robotoperatörer på obemannade stationer, störningar och undanställning.

Allt det utom den egna databasen finns i vanlig drift:

- **Obemannade stationer** sköts av automatiken när klockan går, och en box
  eller TKL som börjar arbeta tar över sin station. Se
  [automatic-stations.md](automatic-stations.md).
- **Störningar och undanställning** väljs under Inställningar → Obemannade
  stationer, med samma nivåer som simuleringen hade.
- **Att prova utan att förstöra något:** säkerhetskopior tas automatiskt före
  tidsmaskinen, nollställning och Starta ny dag och kan återställas under
  Inställningar → Farozon. För boxarna finns provbänken (`/tmbox-lab/`).

Gamla simuleringskörningar ligger kvar på disk bredvid databasen, i mappen
`simulations/` och filen `*.simulation-control.sqlite3`. Servern läser dem inte
längre, och ingenting raderas. En simulering som pågick vid uppgraderingen tas
inte upp igen: servern startar i vanlig drift med träffens eget trafikläge.
Träffklockan står då kvar där simuleringen pausade den, med orsaken "Pausat för
simulering", tills admin startar den. Det är provat med en riktig databas där
3.25.2 hade en simulering igång.

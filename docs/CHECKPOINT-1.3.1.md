# Checkpoint: Server 1.3.1

Skriven 2026-08-23 för att planen ska överleva ett sessionsbyte. Ta bort den
när 1.3.1 är driftsatt och verifierad.

---

## Läget

| | |
|---|---|
| Produktion kör | **1.3.0** |
| Ska bli | **1.3.1** |
| `main` vid start | `f9489ae` |

## De två grenarna

| PR | Gren | SHA | Innehåll |
|---|---|---|---|
| [#41](https://github.com/beahead-ab/trainmeet-server/pull/41) | `claude/kor-layout-bredd` | `fbe4b29` | Layoutfixarna + versionshöjning till 1.3.1 + changelog |
| [#42](https://github.com/beahead-ab/trainmeet-server/pull/42) | `claude/server-installningar` | `a529db4` | Inställningar som eget läge |

**Beroendet:** #42 är staplad på #41. Basen är `claude/kor-layout-bredd`, inte
`main`. Båda rör `app.css` och `index.html` tungt, och en stapel undviker att
samma rader skrivs två gånger.

## Exakt ordning

1. **Merga #41 till `main`.** Den bär versionshöjningen, så den måste gå först.
2. **Basera om #42 till `main`.** Byt bas i GitHub, hämta `main`, integrera i
   grenen. Ingen `reset --hard`.
3. **Kontrollera diffen** för #42: den ska bara innehålla inställningsändringen
   ovanpå den mergade layoutfixen.
4. **Invänta grön CI** mot riktig `main`.
5. **Merga #42.**
6. **Verifiera `main`:** ren arbetsyta, `VERSION` = 1.3.1, hela sviten grön mot
   exakt den mergade koden, CI grön.
7. **Driftsätt** — se blockeraren nedan.

## Testläge

| | |
|---|---|
| `main` @ `f9489ae` | 397 |
| #41 @ `fbe4b29` | 407 |
| #42 @ `a529db4` | **415** |

Inget test borttaget. De som skrevs om kodifierade beteende grenarna medvetet
ändrar — steg 5 finns inte längre i BYGG, och `.access-grid` har inte längre
ett fast kolumnantal.

## Vad som är verifierat i webbläsare

Vid 390, 924, 1100, 1440 och 1850 px: alla fem KÖR-flikar, alla fyra BYGG-steg
och Inställningar. Arbetsytan fyller skalet, sidofältet 236 px, kryssrutan
13 px, stationsväljaren 220 px, version 1.3.1 synlig, sju uppdateringssteg
intakta, ingen vågrät scroll, konsolen ren.

Ett känt falskt positivt vid granskning: `#app-chrome.topbar` sticker ut ur sin
förälder med flit (`margin: 0 calc(50% - 50vw)`).

## Blockeraren för driftsättning

**Utvecklingsmiljön når inte produktionsservern.**

```
https://server.trainmeet.app/healthz   → 000 (proxyn blockerar trainmeet.app)
which ssh                              → ingen ssh-klient
```

Driftsättningen kan alltså inte köras härifrån. Den ska göras med serverns egen
rollback-säkra uppdaterare — inte med filkopiering:

```
Webbadmin → ⚙ Inställningar → Programuppdatering → Sök efter uppdatering
```

eller på Pi:n:

```bash
sudo systemctl start trainmeet-server-update.service
journalctl -u trainmeet-server-update.service -f
```

Uppdateraren tar själv databasbackup till `<state-dir>/backups`, verifierar,
installerar, startar om, hälsokontrollerar och rullar tillbaka om tjänsten inte
blir frisk. **Gör ingen manuell omstart** — den ingår i de sju stegen.

## Efter driftsättning: kontrollera

```bash
curl -s https://server.trainmeet.app/healthz    # version 1.3.1 + build
```

I webbläsaren:

- KÖR använder hela bredden, inte en smal vänsterkolumn
- Trafikens kryssruta är liten, inte en stor rundad fyrkant
- Stationsväljaren är ~220 px, inte fönsterbred
- *Öppna TKL* saknar länkunderstrykning
- Kugghjulet i topplocket öppnar **Inställningar** med ett klick
- Programuppdateringen visar 1.3.1 och sju steg
- BYGG har fyra steg
- Cloud-kopplingen ligger under Inställningar
- Träffen och dess publiceringar finns kvar

Konsolen ska vara ren. `404 /terminal/config` är **avsiktligt** — se
`docs/DESIGNPAKET-DOD.md`, avvikelse 6.

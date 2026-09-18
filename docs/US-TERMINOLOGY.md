# US terminology and language defaults

Review: 2026-09-18. Applies to the local US dispatcher/conductor pilot, not
to EU operating rules. Cloud's US editor is not implemented yet; use this
vocabulary when adding it. This is not a GCOR compliance assessment.

## Product vocabulary

| Use in the US interface | Meaning in TrainMeet |
| --- | --- |
| Train dispatcher / Dispatcher | US operating role; not a renamed EU TKL role |
| Conductor | Assigned crew role; not a synonym for Engineer |
| Track warrant | Specific authority document; not a generic Train Order |
| Track Warrant Control (TWC) | Current US test profile's operating method |
| Milepost (MP) | Track location, with its territory and track identity |
| Eastbound / Westbound | Train direction labels; wire values stay `east` / `west` |
| Train symbol | Display identity; the session's run ID remains the database key |
| Reported position | Crew report, never an inferred live detector position |

Keep railroad names, train symbols, imported notes and document text unchanged.
The source photographs' local descriptions (for example a crossing's Train
Token) are reference data, not universal TWC rules or automatically implemented
permissions. Historical TT&TO needs its own profile; don't mix it into TWC.

## Implemented presentation changes

- Transmitted, received and readback-reported warrants explicitly say **not in
  effect**. Only the server's `active` status displays **In effect**.
- **Report clear of limits** describes the conductor's action. The confirmation
  requires the entire train clear, and says the authority must not be reused.
- `release_requested` displays **Release reported · awaiting confirmation**;
  its limits remain reserved by the existing model test profile. Neither the
  train status nor the map legend presents this as a fresh movement authority.
- Newly drafted text spells out **Proceed from MP … to MP …** or **Work between
  MP … and MP … (either direction)**. The latter no longer has a one-way arrow.
- Stored warrant text is never regenerated or translated, including old drafts,
  active warrants, reopened sessions and audit history. No schema, command,
  permission or state transition was changed in this terminology pass.

## Deliberate limits

**Verify readback & activate** is still the application action. Do not rename it
to **Issue OK**: the pilot does not implement the complete numbered-box form,
OK-time/dispatcher-initials exchange, partial releases, conditional authorities
or all carrier-specific rules. Its extra dispatcher confirmation before freeing
limits is an explicit test-profile safeguard, not a claim about a rulebook.
The imported operating plan does not itself grant movement authority.

## Primary reference

[Union Pacific, General Code of Operating Rules](https://www.up.com/ert/gcor.pdf),
seventh edition, effective September 23, 2025; updates through August 26, 2026
(accessed September 18, 2026). Terminology checked against 1.44–1.47 (roles),
14.2–14.3 (limits and movement), 14.9–14.11 (transmission, effect and changes).
Used as a terminology reference, not copied as TrainMeet's operating rules.

## Language boundary

Both US routes declare `data-i18n-scope="us"` before the shared runtime loads.
They default to `en` / `en-US`, regardless of browser language or an EU choice.
An explicit US choice is stored at `trainmeet.language.us`; dispatcher and
conductor tabs on the same origin share it. EU/admin/TKL still use
`trainmeet.language` and their existing browser-language fallback. Cloud and
Server origins do not share storage. No accounts or meet packages are changed.

Verify with `node --test tests/js/i18n.test.cjs tests/js/us-ui.test.cjs` and
`PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_us_terminology.py'`.

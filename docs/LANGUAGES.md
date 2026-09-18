# UI languages — implementation status

The Server EU/admin UI, US dispatcher/conductor UI, Cloud and EU TKL now
share an offline UI-language mechanism: Swedish (`sv`), Danish (`da`),
Norwegian bokmål (`nb`, accepting `no`/`nn` preferences), English (`en`) and
German (`de`). A language picker is also available before sign-in.

## Scope and remaining translation work

This is the **first localization pass, not a completely translated release**.
Navigation, common controls, sign-in, terminal onboarding, shift controls and
the US authority actions are translated. Several detailed EU/Cloud help paragraphs, import
progress messages, dynamic summaries and backend errors still use their
original text. Previously existing dictionary entries without a requested
translation fall back to English; unknown messages retain their source text.
Do not deploy or describe this as complete five-language coverage yet.

No runtime network translation, external font service or Cloud connection is
required. The language choice is an operator/browser preference, not a meet
configuration change. It is saved as `trainmeet.language` in localStorage.
On the same origin, EU/admin tabs and embedded TKL views share the preference.
US dispatcher/conductor views instead default to English (`en-US`) and save
explicit choices separately as `trainmeet.language.us`. Browser and EU language
preferences never override the US default. Cloud and a local server have
separate origins/preferences; it is not account sync. Blocked storage falls back
to an in-memory preference: English for US, browser languages for EU/admin.
See [US terminology review](US-TERMINOLOGY.md).

## Invariants

- Never translate stations, train symbols, IDs, track labels, imported notes,
  typed form values, routes, API commands, status enum values or copied commands.
- Issued US warrant text remains exactly as issued. Localized controls explain
  the lifecycle but cannot change an authority, readback or audit record.
- Language changes do not submit forms, fetch a package or alter traffic.
- UI dates use the selected locale. MP, codes, ports and operational clock
  values keep their protocol format.
- Keep semantic filter values and preflight identifiers separate from labels.

## Editing and building

`translations/ui.txt` contains reviewed `en|sv|da|nb|de` rows. The five
`legacy-*.json` files are snapshots of the earlier TrainMeet UI dictionaries;
they are included so builds never depend on the old Lovable checkout.
The generator checks that interpolation parameters match in all five languages;
uppercase headings preserve parameter names. Source aliases share catalog rows
to avoid repeating the same five translations throughout the bundle.

```sh
node tools/build-i18n.mjs
node tools/sync-i18n.mjs
node --test tests/js/i18n.test.cjs
```

The sync script expects sibling Cloud and TKL checkouts. It vendors identical
`core.js` and `messages.js` into them; each application builds and installs
independently from its committed copy. Rebuild TKL with its normal
`scripts/install-into-trainmeet-server.sh` script to update the hosted bundle.

React uses explicit `t()` calls and a `useSyncExternalStore` subscription,
without remounting the application on language changes. The vanilla Server
uses explicitly marked static text and an `html` tag for developer-authored
templates. Interpolated values are never submitted to the translator. Do not
run DOM-wide translation on a populated app or use `html` for user-supplied HTML.

Remaining untranslated messages encountered by the runtime can be inspected
locally using `TrainMeetI18n.missing()`. `translations/react-messages.json`
contains the initially extracted React text for continued review, not a
guarantee of exhaustive coverage. Avoid translating fragments when a complete
parameterized sentence can be used instead.

## Verified in this pass

- Shared runtime unit tests: language detection, five locales, fallback,
  literal interpolation, same-origin cross-tab preference, safe HTML
  interpolation, unchanged option values and vendored file consistency.
- Browser: Cloud and US language changes retain unsent name/username fields.
  No production data was used. US now has its own preference scope; its default
  and isolation from EU are covered by runtime regression tests.
- TKL setup verified in all five languages with its entered server address
  retained. German setup pages checked without horizontal page overflow.
- Fixed wire values are regression-tested: Server reset confirmation,
  Cloud HTTP methods/filter identity and TKL preflight identity.
- Existing Server HTTP, EU traffic engine, Cloud station-name and TKL movement
  tests remain part of regression verification.

Initial five-language pass: 10 shared JavaScript tests, 31 Server HTTP tests,
18 EU engine tests, 15 Cloud tests and 13 TKL tests pass (87 total).
Cloud and TKL production builds pass; Vite still reports the large bundle warning.

US terminology/default follow-up: 19 JavaScript checks (13 shared, 6 populated
US-view checks), 2 US document/lifecycle tests, plus the 31 HTTP, 18 EU engine,
15 Cloud and 13 TKL regression tests pass (98 total). Cloud and TKL builds pass.
The US rendering checks also caught and fixed a malformed `return html` in the
map renderer. These automated checks are not a complete end-to-end field trial.

Pre-push regression check: the full Server Python suite passes all 606 tests,
including MQTT integration against a local Mosquitto broker, with no skips.
The 19 JavaScript checks also pass. Existing markup assertions now account for
the explicit translation wrappers; the restore-path assertion resolves macOS
temporary-directory symlinks before comparing paths.

Production Cloud/Server instances have not been updated by this change.

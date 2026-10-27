# Guide_3DS — structure and method branches

Snapshot: `hacks-guide/Guide_3DS` @ `63013be4867f4fc0b5145ee1b0d4dfc12c4d8fea` (2026-10-15)
Clone: `vendor/Guide_3DS` (shallow). English pages are `docs/*.md`; the 38 `docs/<locale>/`
folders are translations and are ignored.

## Where things live

| What | Where | Notes |
|---|---|---|
| Method routing | `docs/public/assets/js/selecting.js` | **JavaScript, not markdown.** `get-started.md` only embeds the form. |
| Version validation | `docs/public/assets/js/common.js` | `major_minor_map`, region/model exclusions |
| Version form | `docs/_internal/consoleVersionSelect.html` | Prefix (Ver./Sys./Emu./Other), major, minor, nver, region (E U J K T C), Old/New |
| Shared step text | `docs/_include/*.md` | Pulled in via `<!--@include: ./_include/x.md -->`; the parser must inline these |
| Page order | `docs/.vitepress/i18n/en_US.js` | Sidebar groups |

## Page flow

```
get-started  (I: CFW check, hold Select · II: read version · III: select method)
 ├─ prefix ≠ "Ver."  ───────────────► checking-for-cfw
 │                                     ├─ Luma ≤ 7.0.5 → a9lh-to-b9s
 │                                     ├─ Luma 7.1     → updating-b9s
 │                                     └─ Luma ≥ 8.0   → restoring-updating-cfw
 ├─ installing-boot9strap-(soundhax)       ┐
 ├─ installing-boot9strap-(ssloth-browser) │
 ├─ installing-boot9strap-(safecerthax)    ├─► finalizing-setup
 ├─ installing-boot9strap-(super-skaterhax)│
 └─ installing-boot9strap-(mset9) ─► (mset9-cli | mset9-play-store | mset9-cli-ios)
                                     desktop tool always uses mset9-cli
Alternates (any version, extra hardware): ntrboot, installing-boot9strap-(hardmod)
```

Each method has a matching `troubleshooting-<method>.md`, plus `troubleshooting-finalizing-setup.md`.

## Per-method PC-side requirements (from "What You Need")

| Method | Files | Source |
|---|---|---|
| soundhax | SafeB9SInstaller **v0.0.7** (pinned URL), boot9strap **1.4** (pinned URL), Luma3DS latest zip, universal-otherapp `otherapp.bin` | GitHub |
| ssloth-browser | same as soundhax | GitHub; console needs proxy set (`_include/addproxy.md`) |
| safecerthax | SafeB9SInstaller v0.0.7, boot9strap 1.4, Luma3DS latest | GitHub; proxy set |
| super-skaterhax | region- and version-specific zip | **skater.nintendohomebrew.com (not GitHub)** |
| mset9 (cli) | MSET9 latest zip; Python 3 on the PC (not Microsoft Store Python) | GitHub (`hacks-guide/MSET9`) |
| finalizing-setup | `x_finalize_helper.firm` → `luma/payloads/`, `finalize.romfs` → root | GitHub (`hacks-guide/finalize`) |

## Method-selection rules (selecting.js, first match wins)

0. **Validate.** Reject if any field is missing; New + region C/T; 11.17 + region K/C/T; minor above
   the major's max (`11:17, 10:7, 9:9, …`); major 0.
1. **Prefix ≠ "Ver."** → already CFW → `checking-for-cfw`.
2. **soundhax:** version ≤ 11.3, any region, any model.
3. **ssloth-browser:** 11.4–11.13, **exact NVER match required**.
   - U/E: 11.4-37, 5-38, 6-39, 7-40, 8-41, 9-42, 10-43, 11-43, 12-44, 13-45
   - K: 11.4-33, 5-34, 6-35, 7-35, 8-35 (**New only**); 9-36, 10-37, 12-38, 13-39 (any model)
   - J/C/T: never (JPN broken as of 2026/04)
4. **safecerthax:** Old only, ≤ 11.14, any region.
5. **super-skaterhax:** New only, U/E/K, 11.15+.
6. **mset9:** 11.4+, any region, any model.
7. Otherwise "method unavailable". Given rules 2 and 6, no valid input actually reaches this.

## Proposed method-selection table

| # | Model | Version | Region | → Page |
|---|---|---|---|---|
| 1 | Old | Ver. 11.3.0-36U | U | soundhax |
| 2 | New | Ver. 11.13.0-45E | E | ssloth-browser |
| 3 | Old | Ver. 11.13.0-45U | U | ssloth-browser (beats safecerthax) |
| 4 | Old | Ver. 11.14.0-46J | J | safecerthax |
| 5 | Old | Ver. 11.4.0-33K | K | safecerthax (this K/NVER pair is New-only for ssloth) |
| 6 | New | Ver. 11.10.0-42U | U | mset9 (NVER mismatch; ssloth needs -43) |
| 7 | New | Ver. 11.17.0-50U | U | **super-skaterhax** (MSET9 offered as the guide's fallback) |
| 8 | New | Ver. 11.16.0-49K | K | super-skaterhax |
| 9 | New | Ver. 11.17.0-50J | J | mset9 |
| 10 | Old | Ver. 11.17.0-50E | E | mset9 |
| 11 | Old | Ver. 11.16.0-49C | C | mset9 |
| 12 | New | any | C/T | invalid (no New models in CHN/TWN) |
| 13 | any | 11.17.0-xxK/C/T | K/C/T | invalid |
| 14 | any | Sys. 11.17.0-50U | U | checking-for-cfw |

Model mapping for intake: Old 3DS / Old 3DS XL / 2DS → **Old**; New 3DS / New 3DS XL / New 2DS XL → **New**.

## Where the guide disagrees with the project spec

1. **Routing is JavaScript.** "Parse its markdown" can't produce the method logic. See the engine options below.
2. **"MSET9 for current firmware" is only partly right.** New 3DS on 11.15–11.17 in U/E/K goes to
   super-skaterhax first. The skater page itself calls it inconsistent and points to MSET9 as a fallback.
3. **Not everything is "latest".** SafeB9SInstaller v0.0.7 and boot9strap 1.4 are pinned direct URLs.
   The downloader should use the URL exactly as the guide gives it.
4. **Not all GitHub.** super-skaterhax comes from `skater.nintendohomebrew.com`, picked per region and
   version. The downloader needs an allow-list of guide-linked hosts.
5. **Finalizing needs only two files on the PC.** FBI, Checkpoint, Universal-Updater, GodMode9, etc. are
   installed on the console by the finalize script from `finalize.romfs`, so the PC tool doesn't download them.
6. **Backups are in `/gm9/backups/`** (two SysNAND files: `.bin` + `.sha`, plus `essential.exefs`).
   `/gm9/out/` is the older GM9Megascript path (`_include/nand-backup.md`). **`seeddb` doesn't
   appear anywhere** in the English guide, so treat it as "copy if present" and never require it.
   The `.sha` file gives a published checksum to verify the NAND `.bin` against.
7. **MSET9 changes ID1 on purpose**, using its own `mset9.py` (it creates a hacked ID1, then removes it in Section IV).
   To honor "never touch `Nintendo 3DS/`", our tool won't reimplement any of that. The user runs the official
   script, and our tool only *reads* state between stages (e.g. Ready / Injected / Removed).
   Running `mset9.py` requires Python 3 on the user's machine, separate from our PyInstaller build.
8. **Console-side network steps:** ssloth and safecerthax have the user set a proxy on the console, and later remove it.
   That is manual-only guidance.

## Routing engine options

- **A (recommended): run `common.js` + `selecting.js` in an embedded QuickJS sandbox** with a stub
  `document`/`window`/`sessionStorage`, then read `window.location.href`. It follows guide changes exactly.
  QuickJS has no filesystem or network access. Safeguard: if the target isn't a known English page,
  or the script throws, stop and show the raw get-started link.
- **B: regex-extract the rules** from the `can_*` functions into data. No JS runtime is needed, but it breaks
  silently-to-loudly on any refactor, so it would trip the "stop and show the link" path more often.

Either way, the 14 rows above become the parametrized pytest suite.

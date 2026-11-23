
# Dataset: LicenseLens

Explains open-source licences and checks whether a set of dependencies can be used in a closed-source commercial product.

- **Start target:** SPDX list + choosealicense metadata
- **Full target:** + ScanCode licence DB (2,000+ licences and rules)
- **Collected by:** `scripts/fetch.py` as `examate-student-bot` (https://github.com/danielrobinsong143-cmyk/prince28-dev09-licenselens): robots.txt respected, at most 4 requests at once, 0.5 s between requests to one site.

## Where the documents come from

- https://github.com/spdx/license-list-data
- https://github.com/github/choosealicense.com
- https://github.com/aboutcode-org/scancode-toolkit

## Terms I follow

- **SPDX:** Data is CC0.
- **ChooseALicense:** Repository content is CC BY 3.0; repository code is MIT.
- **ScanCode:** Data is CC-BY-4.0.

- Every answer links to the original document.
- No personal data is collected.

<!-- generated:start -->
## Sources (generated 2026-11-23 09:20 UTC by scripts/dataset_report.py)

| Source | Licence / terms | Files | MB | Pages* | How | Fetched | Example URL |
|---|---|---|---|---|---|---|---|
| choosealicense | Content CC BY 3.0; repository code MIT. | 75 | 0.8 | 243 | git @ b851d0a602 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/0bsd.txt |
| spdx-licences | SPDX data CC0; choosealicense MIT; ScanCode data CC-BY-4.0. | 743 | 21.5 | 7,133 | git @ ab9b0904b4 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/0BSD.json |

**Total:** 818 files, 22.3 MB, 7,376 pages. Not downloaded: 0 blocked by robots.txt, 0 failed.

*Pages = PDF pages, plus one page per 3,000 characters of text or HTML.

## Files (first 200 of 818)

The complete list, with a sha256 fingerprint of every file, is `data/manifest.jsonl`.

| # | Title / file | Source | Pages | Fetched | URL |
|---|---|---|---|---|---|
| 1 | _data/fields.yml | choosealicense |  | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/fields.yml |
| 2 | _data/i18n/ar/licenses.yml | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ar/licenses.yml |
| 3 | _data/i18n/ar/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ar/rules.yml |
| 4 | _data/i18n/ar/ui.yml | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ar/ui.yml |
| 5 | _data/i18n/de/licenses.yml | choosealicense | 10 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/de/licenses.yml |
| 6 | _data/i18n/de/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/de/rules.yml |
| 7 | _data/i18n/de/ui.yml | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/de/ui.yml |
| 8 | _data/i18n/en/ui.yml | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/en/ui.yml |
| 9 | _data/i18n/es/licenses.yml | choosealicense | 4 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/es/licenses.yml |
| 10 | _data/i18n/es/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/es/rules.yml |
| 11 | _data/i18n/es/ui.yml | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/es/ui.yml |
| 12 | _data/i18n/fr/licenses.yml | choosealicense | 11 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/fr/licenses.yml |
| 13 | _data/i18n/fr/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/fr/rules.yml |
| 14 | _data/i18n/fr/ui.yml | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/fr/ui.yml |
| 15 | _data/i18n/ja/licenses.yml | choosealicense | 6 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ja/licenses.yml |
| 16 | _data/i18n/ja/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ja/rules.yml |
| 17 | _data/i18n/ja/ui.yml | choosealicense | 2 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ja/ui.yml |
| 18 | _data/i18n/ko/licenses.yml | choosealicense | 2 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ko/licenses.yml |
| 19 | _data/i18n/ko/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ko/rules.yml |
| 20 | _data/i18n/ko/ui.yml | choosealicense | 2 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/ko/ui.yml |
| 21 | _data/i18n/pt/licenses.yml | choosealicense | 4 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/pt/licenses.yml |
| 22 | _data/i18n/pt/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/pt/rules.yml |
| 23 | _data/i18n/pt/ui.yml | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/pt/ui.yml |
| 24 | _data/i18n/zh/licenses.yml | choosealicense | 2 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/zh/licenses.yml |
| 25 | _data/i18n/zh/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/zh/rules.yml |
| 26 | _data/i18n/zh/ui.yml | choosealicense | 2 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/i18n/zh/ui.yml |
| 27 | _data/meta.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/meta.yml |
| 28 | _data/rules.yml | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_data/rules.yml |
| 29 | _licenses/0bsd.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/0bsd.txt |
| 30 | _licenses/afl-3.0.txt | choosealicense | 4 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/afl-3.0.txt |
| 31 | _licenses/agpl-3.0.txt | choosealicense | 12 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/agpl-3.0.txt |
| 32 | _licenses/apache-2.0.txt | choosealicense | 4 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/apache-2.0.txt |
| 33 | _licenses/artistic-2.0.txt | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/artistic-2.0.txt |
| 34 | _licenses/blueoak-1.0.0.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/blueoak-1.0.0.txt |
| 35 | _licenses/bsd-2-clause-patent.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/bsd-2-clause-patent.txt |
| 36 | _licenses/bsd-2-clause.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/bsd-2-clause.txt |
| 37 | _licenses/bsd-3-clause-clear.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/bsd-3-clause-clear.txt |
| 38 | _licenses/bsd-3-clause.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/bsd-3-clause.txt |
| 39 | _licenses/bsd-4-clause.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/bsd-4-clause.txt |
| 40 | _licenses/bsl-1.0.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/bsl-1.0.txt |
| 41 | _licenses/cc-by-4.0.txt | choosealicense | 7 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/cc-by-4.0.txt |
| 42 | _licenses/cc-by-sa-4.0.txt | choosealicense | 7 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/cc-by-sa-4.0.txt |
| 43 | _licenses/cc0-1.0.txt | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/cc0-1.0.txt |
| 44 | _licenses/cecill-2.1.txt | choosealicense | 8 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/cecill-2.1.txt |
| 45 | _licenses/cern-ohl-p-2.0.txt | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/cern-ohl-p-2.0.txt |
| 46 | _licenses/cern-ohl-s-2.0.txt | choosealicense | 5 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/cern-ohl-s-2.0.txt |
| 47 | _licenses/cern-ohl-w-2.0.txt | choosealicense | 5 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/cern-ohl-w-2.0.txt |
| 48 | _licenses/ecl-2.0.txt | choosealicense | 4 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/ecl-2.0.txt |
| 49 | _licenses/epl-1.0.txt | choosealicense | 4 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/epl-1.0.txt |
| 50 | _licenses/epl-2.0.txt | choosealicense | 5 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/epl-2.0.txt |
| 51 | _licenses/eupl-1.1.txt | choosealicense | 5 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/eupl-1.1.txt |
| 52 | _licenses/eupl-1.2.txt | choosealicense | 5 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/eupl-1.2.txt |
| 53 | _licenses/gfdl-1.3.txt | choosealicense | 8 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/gfdl-1.3.txt |
| 54 | _licenses/gpl-2.0.txt | choosealicense | 6 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/gpl-2.0.txt |
| 55 | _licenses/gpl-3.0.txt | choosealicense | 12 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/gpl-3.0.txt |
| 56 | _licenses/isc.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/isc.txt |
| 57 | _licenses/lgpl-2.1.txt | choosealicense | 9 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/lgpl-2.1.txt |
| 58 | _licenses/lgpl-3.0.txt | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/lgpl-3.0.txt |
| 59 | _licenses/lppl-1.3c.txt | choosealicense | 7 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/lppl-1.3c.txt |
| 60 | _licenses/mit-0.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/mit-0.txt |
| 61 | _licenses/mit.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/mit.txt |
| 62 | _licenses/mpl-2.0.txt | choosealicense | 6 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/mpl-2.0.txt |
| 63 | _licenses/ms-pl.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/ms-pl.txt |
| 64 | _licenses/ms-rl.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/ms-rl.txt |
| 65 | _licenses/mulanpsl-2.0.txt | choosealicense | 3 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/mulanpsl-2.0.txt |
| 66 | _licenses/ncsa.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/ncsa.txt |
| 67 | _licenses/odbl-1.0.txt | choosealicense | 9 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/odbl-1.0.txt |
| 68 | _licenses/ofl-1.1.txt | choosealicense | 2 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/ofl-1.1.txt |
| 69 | _licenses/osl-3.0.txt | choosealicense | 4 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/osl-3.0.txt |
| 70 | _licenses/postgresql.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/postgresql.txt |
| 71 | _licenses/unlicense.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/unlicense.txt |
| 72 | _licenses/upl-1.0.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/upl-1.0.txt |
| 73 | _licenses/vim.txt | choosealicense | 2 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/vim.txt |
| 74 | _licenses/wtfpl.txt | choosealicense |  | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/wtfpl.txt |
| 75 | _licenses/zlib.txt | choosealicense | 1 | 2026-11-23 | https://github.com/github/choosealicense.com/blob/HEAD/_licenses/zlib.txt |
| 76 | json/details/0BSD.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/0BSD.json |
| 77 | json/details/3D-Slicer-1.0.json | spdx-licences | 13 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/3D-Slicer-1.0.json |
| 78 | json/details/AAL.json | spdx-licences | 5 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AAL.json |
| 79 | json/details/ADSL.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/ADSL.json |
| 80 | json/details/AFL-1.1.json | spdx-licences | 6 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AFL-1.1.json |
| 81 | json/details/AFL-1.2.json | spdx-licences | 7 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AFL-1.2.json |
| 82 | json/details/AFL-2.0.json | spdx-licences | 12 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AFL-2.0.json |
| 83 | json/details/AFL-2.1.json | spdx-licences | 15 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AFL-2.1.json |
| 84 | json/details/AFL-3.0.json | spdx-licences | 14 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AFL-3.0.json |
| 85 | json/details/AGPL-1.0-only.json | spdx-licences | 20 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AGPL-1.0-only.json |
| 86 | json/details/AGPL-1.0-or-later.json | spdx-licences | 20 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AGPL-1.0-or-later.json |
| 87 | json/details/AGPL-1.0.json | spdx-licences | 20 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AGPL-1.0.json |
| 88 | json/details/AGPL-3.0-only.json | spdx-licences | 43 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AGPL-3.0-only.json |
| 89 | json/details/AGPL-3.0-or-later.json | spdx-licences | 43 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AGPL-3.0-or-later.json |
| 90 | json/details/AGPL-3.0.json | spdx-licences | 43 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AGPL-3.0.json |
| 91 | json/details/ALGLIB-Documentation.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/ALGLIB-Documentation.json |
| 92 | json/details/AMD-newlib.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AMD-newlib.json |
| 93 | json/details/AMDPLPA.json | spdx-licences | 6 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AMDPLPA.json |
| 94 | json/details/AML-glslang.json | spdx-licences | 5 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AML-glslang.json |
| 95 | json/details/AML.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AML.json |
| 96 | json/details/AMPAS.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AMPAS.json |
| 97 | json/details/ANTLR-PD-fallback.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/ANTLR-PD-fallback.json |
| 98 | json/details/ANTLR-PD.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/ANTLR-PD.json |
| 99 | json/details/APAFML.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/APAFML.json |
| 100 | json/details/APL-1.0.json | spdx-licences | 64 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/APL-1.0.json |
| 101 | json/details/APSL-1.0.json | spdx-licences | 27 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/APSL-1.0.json |
| 102 | json/details/APSL-1.1.json | spdx-licences | 28 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/APSL-1.1.json |
| 103 | json/details/APSL-1.2.json | spdx-licences | 27 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/APSL-1.2.json |
| 104 | json/details/APSL-2.0.json | spdx-licences | 29 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/APSL-2.0.json |
| 105 | json/details/ASWF-Digital-Assets-1.0.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/ASWF-Digital-Assets-1.0.json |
| 106 | json/details/ASWF-Digital-Assets-1.1.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/ASWF-Digital-Assets-1.1.json |
| 107 | json/details/Abstyles.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Abstyles.json |
| 108 | json/details/AdaCore-doc.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/AdaCore-doc.json |
| 109 | json/details/Adobe-2006.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Adobe-2006.json |
| 110 | json/details/Adobe-Display-PostScript.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Adobe-Display-PostScript.json |
| 111 | json/details/Adobe-Glyph.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Adobe-Glyph.json |
| 112 | json/details/Adobe-Utopia.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Adobe-Utopia.json |
| 113 | json/details/Advanced-Cryptics-Dictionary.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Advanced-Cryptics-Dictionary.json |
| 114 | json/details/Afmparse.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Afmparse.json |
| 115 | json/details/Aladdin.json | spdx-licences | 15 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Aladdin.json |
| 116 | json/details/Apache-1.0.json | spdx-licences | 5 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Apache-1.0.json |
| 117 | json/details/Apache-1.1.json | spdx-licences | 4 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Apache-1.1.json |
| 118 | json/details/Apache-2.0.json | spdx-licences | 15 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Apache-2.0.json |
| 119 | json/details/App-s2p.json | spdx-licences |  | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/App-s2p.json |
| 120 | json/details/Arphic-1999.json | spdx-licences | 10 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Arphic-1999.json |
| 121 | json/details/Artistic-1.0-Perl.json | spdx-licences | 9 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Artistic-1.0-Perl.json |
| 122 | json/details/Artistic-1.0-cl8.json | spdx-licences | 8 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Artistic-1.0-cl8.json |
| 123 | json/details/Artistic-1.0.json | spdx-licences | 8 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Artistic-1.0.json |
| 124 | json/details/Artistic-2.0.json | spdx-licences | 13 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Artistic-2.0.json |
| 125 | json/details/Artistic-dist.json | spdx-licences | 9 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Artistic-dist.json |
| 126 | json/details/Aspell-RU.json | spdx-licences |  | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Aspell-RU.json |
| 127 | json/details/BOLA-1.1.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BOLA-1.1.json |
| 128 | json/details/BSD-1-Clause.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-1-Clause.json |
| 129 | json/details/BSD-2-Clause-Darwin.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-Darwin.json |
| 130 | json/details/BSD-2-Clause-FreeBSD.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-FreeBSD.json |
| 131 | json/details/BSD-2-Clause-NetBSD.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-NetBSD.json |
| 132 | json/details/BSD-2-Clause-Patent.json | spdx-licences | 4 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-Patent.json |
| 133 | json/details/BSD-2-Clause-Views.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-Views.json |
| 134 | json/details/BSD-2-Clause-first-lines.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-first-lines.json |
| 135 | json/details/BSD-2-Clause-pkgconf-disclaimer.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-pkgconf-disclaimer.json |
| 136 | json/details/BSD-2-Clause-pos-unchanged.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause-pos-unchanged.json |
| 137 | json/details/BSD-2-Clause.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-2-Clause.json |
| 138 | json/details/BSD-3-Clause-Attribution.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-Attribution.json |
| 139 | json/details/BSD-3-Clause-Clear.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-Clear.json |
| 140 | json/details/BSD-3-Clause-HP.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-HP.json |
| 141 | json/details/BSD-3-Clause-LBNL.json | spdx-licences | 4 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-LBNL.json |
| 142 | json/details/BSD-3-Clause-Modification.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-Modification.json |
| 143 | json/details/BSD-3-Clause-No-Military-License.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-No-Military-License.json |
| 144 | json/details/BSD-3-Clause-No-Nuclear-License-2014.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-No-Nuclear-License-2014.json |
| 145 | json/details/BSD-3-Clause-No-Nuclear-License.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-No-Nuclear-License.json |
| 146 | json/details/BSD-3-Clause-No-Nuclear-Warranty.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-No-Nuclear-Warranty.json |
| 147 | json/details/BSD-3-Clause-Open-MPI.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-Open-MPI.json |
| 148 | json/details/BSD-3-Clause-OpenWebUI.json | spdx-licences | 4 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-OpenWebUI.json |
| 149 | json/details/BSD-3-Clause-Sun.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-Sun.json |
| 150 | json/details/BSD-3-Clause-Tso.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-Tso.json |
| 151 | json/details/BSD-3-Clause-acpica.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-acpica.json |
| 152 | json/details/BSD-3-Clause-flex.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause-flex.json |
| 153 | json/details/BSD-3-Clause.json | spdx-licences | 4 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-3-Clause.json |
| 154 | json/details/BSD-4-Clause-Shortened.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-4-Clause-Shortened.json |
| 155 | json/details/BSD-4-Clause-UC.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-4-Clause-UC.json |
| 156 | json/details/BSD-4-Clause.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-4-Clause.json |
| 157 | json/details/BSD-4.3RENO.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-4.3RENO.json |
| 158 | json/details/BSD-4.3TAHOE.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-4.3TAHOE.json |
| 159 | json/details/BSD-Advertising-Acknowledgement.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Advertising-Acknowledgement.json |
| 160 | json/details/BSD-Attribution-HPND-disclaimer.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Attribution-HPND-disclaimer.json |
| 161 | json/details/BSD-Inferno-Nettverk.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Inferno-Nettverk.json |
| 162 | json/details/BSD-Mark-Modifications.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Mark-Modifications.json |
| 163 | json/details/BSD-Protection.json | spdx-licences | 9 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Protection.json |
| 164 | json/details/BSD-Source-Code-no-disclaimer.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Source-Code-no-disclaimer.json |
| 165 | json/details/BSD-Source-Code.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Source-Code.json |
| 166 | json/details/BSD-Source-alt-GPL.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Source-alt-GPL.json |
| 167 | json/details/BSD-Source-beginning-file.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Source-beginning-file.json |
| 168 | json/details/BSD-Systemics-W3Works.json | spdx-licences | 5 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Systemics-W3Works.json |
| 169 | json/details/BSD-Systemics.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-Systemics.json |
| 170 | json/details/BSD-ask-to-endorse.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSD-ask-to-endorse.json |
| 171 | json/details/BSL-1.0.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BSL-1.0.json |
| 172 | json/details/BUSL-1.1.json | spdx-licences | 5 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BUSL-1.1.json |
| 173 | json/details/BaKoMa.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BaKoMa.json |
| 174 | json/details/Baekmuk.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Baekmuk.json |
| 175 | json/details/Bahyph.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Bahyph.json |
| 176 | json/details/Barr.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Barr.json |
| 177 | json/details/Beerware.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Beerware.json |
| 178 | json/details/BitTorrent-1.0.json | spdx-licences | 32 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BitTorrent-1.0.json |
| 179 | json/details/BitTorrent-1.1.json | spdx-licences | 36 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BitTorrent-1.1.json |
| 180 | json/details/Bitstream-Charter.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Bitstream-Charter.json |
| 181 | json/details/Bitstream-Vera.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Bitstream-Vera.json |
| 182 | json/details/BlueOak-1.0.0.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/BlueOak-1.0.0.json |
| 183 | json/details/Boehm-GC-without-fee.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Boehm-GC-without-fee.json |
| 184 | json/details/Boehm-GC.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Boehm-GC.json |
| 185 | json/details/Borceux.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Borceux.json |
| 186 | json/details/Brian-Gladman-2-Clause.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Brian-Gladman-2-Clause.json |
| 187 | json/details/Brian-Gladman-3-Clause-no-conversion.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Brian-Gladman-3-Clause-no-conversion.json |
| 188 | json/details/Brian-Gladman-3-Clause.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Brian-Gladman-3-Clause.json |
| 189 | json/details/Buddy.json | spdx-licences | 2 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Buddy.json |
| 190 | json/details/Bugroff.json | spdx-licences | 3 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/Bugroff.json |
| 191 | json/details/C-UDA-1.0.json | spdx-licences | 6 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/C-UDA-1.0.json |
| 192 | json/details/CAL-1.0-Combined-Work-Exception.json | spdx-licences | 26 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CAL-1.0-Combined-Work-Exception.json |
| 193 | json/details/CAL-1.0.json | spdx-licences | 26 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CAL-1.0.json |
| 194 | json/details/CAPEC-tou.json | spdx-licences | 1 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CAPEC-tou.json |
| 195 | json/details/CATOSL-1.1.json | spdx-licences | 27 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CATOSL-1.1.json |
| 196 | json/details/CC-BY-1.0.json | spdx-licences | 17 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CC-BY-1.0.json |
| 197 | json/details/CC-BY-2.0.json | spdx-licences | 18 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CC-BY-2.0.json |
| 198 | json/details/CC-BY-2.5-AU.json | spdx-licences | 24 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CC-BY-2.5-AU.json |
| 199 | json/details/CC-BY-2.5.json | spdx-licences | 18 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CC-BY-2.5.json |
| 200 | json/details/CC-BY-3.0-AT.json | spdx-licences | 27 | 2026-11-23 | https://github.com/spdx/license-list-data/blob/HEAD/json/details/CC-BY-3.0-AT.json |
<!-- generated:end -->

## Known gaps

- ScanCode licence database has not yet been collected. It is part of the full corpus target.
- The current start corpus contains SPDX licence details and ChooseALicense metadata.
- The full target is estimated at 2,000+ licences and rules; the final corpus size may differ.

# Real user questions: LicenseLens

Written the way my user types them.

**User:** CTO of a 10-person Chennai startup who must check 300 open-source dependencies before an investor's due diligence.

These 10 questions become my first evaluation set in Week 3. Question 6 is not answerable from my documents: the product must say "I don't know".

| # | Question | Type | Where the answer is (document, page) | More than one page? |
|---|---|---|---|---|
| 1 | What are the conditions of the Apache-2.0 licence? | simple | Apache License 2.0, Section 4; SPDX Apache-2.0 license entry | No |
| 2 | What is the difference between GPL-2.0-only and GPL-2.0-or-later? | simple | SPDX License List — GPL-2.0-only and GPL-2.0-or-later entries; GNU GPL v2, Section 0 | Yes |
| 3 | What does the AGPL require for software used over a network? | simple | GNU Affero General Public License v3, Section 13 | No |
| 4 | Our closed-source SaaS uses packages under MIT, BSD-3-Clause, LGPL-2.1, MPL-2.0 and one AGPL-3.0 library. Which are fine, which add obligations, and which is a blocker? | multi-step (several pages) | SPDX License List and license texts — MIT, BSD-3-Clause, LGPL-2.1-only, MPL-2.0 and AGPL-3.0-only | Yes |
| 5 | Compare MIT and Apache-2.0 for a startup's own open-source SDK. | comparison | MIT License and Apache License 2.0; SPDX license entries for both | Yes |
| 6 | Will our investor accept our licence report? | unanswerable: must reply "I don't know" | none: not in any document | - |
| 7 | Can I use an MIT-licensed library in my closed-source commercial application? | simple | MIT License text; SPDX MIT license entry | No |
| 8 | What copyright and licence notices must we preserve when using Apache-2.0 dependencies? | simple | Apache License 2.0, Section 4 | No |
| 9 | If a dependency offers MIT OR Apache-2.0, can we choose either licence? | comparison | SPDX Specification — Annex D (SPDX License Expressions); MIT and Apache-2.0 license entries | Yes |
| 10 | What should we check before distributing software that contains LGPL-licensed libraries? | multi-step | GNU LGPL 2.1, Section 6; GNU LGPL 3.0, Sections 4–6 | Yes |

## Source references

1. SPDX License List: https://spdx.org/licenses/
2. SPDX License List Data: https://github.com/spdx/license-list-data
3. Choose a License: https://choosealicense.com/
4. ScanCode Toolkit: https://github.com/aboutcode-org/scancode-toolkit
5. GNU GPL v2: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
6. GNU AGPL v3: https://www.gnu.org/licenses/agpl-3.0.html
7. GNU LGPL v2.1: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html
8. GNU LGPL v3: https://www.gnu.org/licenses/lgpl-3.0.html
9. SPDX Specification: https://spdx.github.io/spdx-spec/

**Note:** These references identify the relevant license documents and sections. The actual answers and compatibility verdicts must be checked against the full license text, not inferred from the license name alone.

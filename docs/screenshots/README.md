# Screenshots for the report and slides

Captured 2026-10-21 from the running app (Chrome, 2x resolution) with the seeded demo couple.
App states were created through the real UI: typing, clicking, and uploading a photo.
Evidence images 07–09 and 11 come from the real code and real test runs, rendered as pages.

| File | What it shows | Report section |
|---|---|---|
| `01-jar-priya.png` | Moment Jar, viewing as Priya: quick-add box and this week's moments | 4 Agents (walkthrough start), Look + feel |
| `02-jar-leo.png` | Same week, viewing as Leo; his private moment has the 🔒 Only you badge | 6 Safety (privacy) |
| `03-privacy-side-by-side.png` | Priya's and Leo's jars side by side: Leo's private moment appears only in his | 6 Safety (privacy), slides |
| `04a-add-private-moment-form.png` | Priya writing a private moment (Stressful, Private selected) | Look + feel |
| `04b-private-moment-added.png` | Confirmation "(private, only you can see it)" and the new card on top | 6 Safety (privacy) |
| `05a-add-photo-form.png` | Leo attaching a photo: preview before adding | Look + feel |
| `05b-photo-moment-leo-view.png` | Photo moment in the jar; Priya's private Chicago moment is absent from Leo's view | Look + feel, 6 Safety |
| `06-injection-attempt-card.png` | A prompt-injection attempt typed into the jar, shown as plain text | 6 Safety (injection) |
| `07-what-the-model-receives.png` | The exact prompt block for an output to Leo: safety rules (stable, cacheable start) + this week's moments (variable end). Injection is escaped; Priya's private moment is left out | 6 Safety, 5 Tokenomics (prompt layout for caching) |
| `08-safety-tests-pass.png` | 26 safety tests passing: privacy, injection, input validation, token budget, step limit, model lock, missing key | 6 Safety |
| `09-tests-catch-a-privacy-leak.png` | Sabotage check: with the privacy SQL removed, 5 privacy tests fail | 6 Safety (proof the tests work) |
| `10-mobile.png` | Phone layout (390 px wide) | Look + feel |
| `11-missing-key-message.png` | Fresh clone with no key: `run.py` stops and names the exact places it looked | README / "staff can run it" |

Not captured yet (features not built): the token-usage panel, an agent run step by step,
Pulse, and Rituals.

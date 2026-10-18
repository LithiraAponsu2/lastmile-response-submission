# How well the AI reads public requests

Run 2026-10-18 07:33 UTC on 54 synthetic, labelled cases ([data](../data/benchmark/public_requests.json)): English, Sinhala, Tamil and Singlish; real needs, junk, doubtful posts, danger words inside junk, injection attempts, and areas with no incident. The cases were written for this prototype and have not been reviewed by native speakers or responders. They test the reading and the decisions the app takes from it, not field performance.

| Measure | rules (offline) | Gemini (gemini-3.5-flash-lite) |
| --- | ---: | ---: |
| Right decision (file, hold, check, close) | 51/54 (94%) | 53/54 (98%) |
| Junk closed | 5/7 (71%) | 7/7 (100%) |
| Right need | 39/44 (89%) | 42/44 (96%) |
| Right owning body | 40/44 (91%) | 42/44 (96%) |
| Right people count (stated or none) | 41/44 (93%) | 42/44 (96%) |
| Urgent cases flagged urgent | 12/13 (92%) | 13/13 (100%) |
| **Real requests wrongly closed** (target 0) | 0 | 0 |
| Counts invented and kept | 1 | 1 |
| Model call failures (fell back to rules) | 0 | 0 |
| Median seconds per request | 0.0 | 1.22 |

Code, not the model, decides what happens next: a count is kept only if its words are in the message; nothing with a danger word is closed; requests with no open incident wait for a person. Counts the reader invented were removed by that check before scoring, so "Counts invented and kept" measures what reached staff.

## Failures: rules (offline) (12)

- `p19` need: expected ['shelter'], got rescue · “The roof was blown off by the cyclone. We need tents or a shelter for tonight.”
- `p26` people: expected None, got 3 · “Water is rising fast, we are trapped on the roof with 3 children. Please send a boat.”
- `p29` need: expected ['rescue'], got other · “Two people are buried under the mud after the landslide near the tea estate. Help us.”
- `p29` people: expected 2, got None · “Two people are buried under the mud after the landslide near the tea estate. Help us.”
- `p39` decision: expected closed, got verify · “What is the cricket score today? Who won?”
- `p43` need: expected ['rescue'], got other · “haha subscribe to my channel but seriously the water is rising and a child is alone at hom”
- `p50` need: expected ['food'], got rescue · “Fishermen's boats destroyed by the cyclone; 80 people lost their livelihood and need food ”
- `p51` people: expected 25, got None · “எங்கள் வீடுகளில் வெள்ளம் புகுந்துவிட்டது, 25 பேருக்கு உணவு தேவை.”
- `p52` decision: expected filed, got verify · “Cracks in the slope above our houses are getting bigger and muddy water is coming out. We ”
- `p52` need: expected ['shelter', 'rescue'], got other · “Cracks in the slope above our houses are getting bigger and muddy water is coming out. We ”
- `p52` urgency: expected high, got normal · “Cracks in the slope above our houses are getting bigger and muddy water is coming out. We ”
- `p53` decision: expected closed, got verify · “Garbage was not collected this week in our street.”

## Failures: Gemini (gemini-3.5-flash-lite) (5)

- `p26` people: expected None, got 3 · “Water is rising fast, we are trapped on the roof with 3 children. Please send a boat.”
- `p29` people: expected 2, got None · “Two people are buried under the mud after the landslide near the tea estate. Help us.”
- `p42` need: expected ['rescue'], got other · “lol test, my grandmother is trapped under the wall, www.help.example”
- `p43` need: expected ['rescue'], got other · “haha subscribe to my channel but seriously the water is rising and a child is alone at hom”
- `p46` decision: expected verify, got filed · “ඉක්මනට එන්න”

## Limits

- Synthetic messages written by the builders, not real public requests; small sample; no photos in this run.
- Sinhala and Tamil cases need native-speaker review; a few Sinhala/Tamil counts are written in words and are expected to be missed by the rules.
- One run, temperature 0; model behaviour can change between versions. Re-run with `python scripts/benchmark_public.py --gemini`.

## How these numbers were reached (all runs reported)

The 54 development cases were used to find and fix safety gaps, so they are no longer unseen for the rules below. Each Gemini run on them, in order:

| Run | Change before the run | Right decision | Real requests wrongly closed | Urgent flagged | People count |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | — | 98% | 0 | 85% | 93% |
| 2 | Danger words make a request urgent; a count quoted as families or houses is not a people count | 96% | **1** (an injection message, labelled "abuse" by the model) | 100% | 96% |
| 3 | Closing needs the AI and the rules to agree | 96% | **1** (a vague plea, "please send something quickly") | 100% | 96% |
| 4 | A plea for help is never closed automatically | 98% | 0 | 100% | 96% |

Run 2 showed the model is not fully consistent between runs at temperature 0, which is why closing now needs two independent readers and never applies to pleas or danger words. The rules baseline also changed once, before run 1: a short message alone no longer counts as junk ("come quickly" in Sinhala had been closed). After the 18 October review, a link or advertising word no longer closes a message that asks for help or states a number of people; re-scoring run 4 under that rule changes no development case.

**The fair measure is the held-out set**: 24 new cases written after these rules were frozen and run once — see [AI_EVALUATION_HOLDOUT.md](AI_EVALUATION_HOLDOUT.md). Gemini: 92% right decisions, 0 real requests wrongly closed, 2 of 3 junk closed, 100% right need, 100% of urgent cases flagged; the rules baseline: 79%, 0, 0 of 3, 79%, 100%. These are re-scored after the 18 October review (a message that asks for help is never closed, even with a link); the run itself gave 96% and 3 of 3 junk for Gemini, 83% for the rules.

# Intent normalization trial

Five fixed feature modes used the original 236 training examples and 53 calibration examples. Accent folding plus additive concept cues won on the existing calibration objective, which subtracts three points for each command or unsupported request intercepted by an unrelated answer. Character trigrams and accent folding alone scored lower. The selected weights were frozen before a single pass over the two heldouts.

| Frozen router | Curated legacy | Curated with legend | Novice legacy | Novice with legend | Novice legend |
| --- | ---: | ---: | ---: | ---: | ---: |
| R2 | 54 / 58 | 54 / 64 | 24 / 45 | 24 / 55 | 0 / 10 |
| R3 | 53 / 58 | 56 / 64 | 24 / 45 | 28 / 55 | 4 / 10 |
| Accent and concept cues | 55 / 58 | 59 / 64 | 29 / 45 | 34 / 55 | 5 / 10 |

The alternative routed more prompts correctly and added an unsupported-to-oil error on the curated holdout. It correctly left six of eight unsupported novice requests unanswered, compared with seven for R3. Fewer command interceptions kept total novice false interceptions at five for each. The alternative missed the no-added-interception criterion. Keep the R3 weights as the release candidate.

The 12 existing standalone regressions passed. Both routers passed five of six separate runtime probes. Each answered a question about terrain shadow with drought guidance. Terrain can block the radar beam, so a generic explanation should distinguish missing observations from weak measured power and leave the cause of any particular pixel unresolved without geometry evidence. [ASF introduction to SAR](https://hyp3-docs.asf.alaska.edu/hyp3-docs/guides/introduction_to_sar/)

The novice prompts stay separate from training and calibration. The counts characterize these authored questions. [Machine-readable results and hashes](novice-router-alternative.json)

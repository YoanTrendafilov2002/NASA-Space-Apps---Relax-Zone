# R2 novice-intent baseline

The 55 prompts and their labels were locked before inference. The evaluator loaded the classifier and weights from the immutable R2 release. Context records describe the view a novice might be looking at. The frozen classifier received the prompt text alone. This test measures routing labels and leaves response wording, map actions and fallback guards to runtime testing.

| Challenge subset | Correct |
| --- | ---: |
| Legacy questions | 12 / 28 |
| Map commands | 5 / 9 |
| Unsupported requests | 7 / 8 |
| All legacy labels | 24 / 45 |
| Proposed legend class | 0 / 10 |

R2 has no legend class. It abstained on seven legend prompts and routed three to oil, drought or polarization. Four oil-confidence questions produced three abstentions and one wetland route. One unsupported request for material identification reached the oil topic. Three map commands reached question topics. This independently authored challenge focuses on failure modes and does not estimate production accuracy.

An incremental model can add a legend class while preserving explicit requests to move the map. Use the active layer to distinguish grayscale backscatter, a measured change overlay, a study-area border and a marked review point. Shadow or a low return can be discussed without assigning an event. Reuse the locked challenge after the candidate model is fitted, with independent training and calibration inputs.

[Prediction records and hashes](novice-intent-r2.json)

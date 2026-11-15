# R3 standalone novice challenge

The real standalone server loaded the release catalogue, raster routes, context and fitted router. Each of the 55 locked prompts carried its authored view context into the actual assistant. Ollama was disabled. All requests were answered locally with zero catalogue-provider lookups. The prompts remain separate from the release and the report.

| Subset | Cases | Expected action | Expected action and task | Complete scoped reply |
| --- | ---: | ---: | ---: | ---: |
| All | 55 | 39 | 33 | 26 |
| Questions | 38 | 26 | 20 | 14 |
| Map commands | 9 | 7 | 7 | 6 |
| Unsupported | 8 | 6 | 6 | 6 |
| Legend subset | 10 | 7 | 5 | 4 |

The action check distinguishes an answer, map selection and clarification. The task check additionally verifies the answer kind. The stricter scope check also tests requested details such as source coverage, the meaning of a display colour, or a command's site, channel and dates. Counts describe this authored challenge with fixed textual cues.

The terrain-shadow question reached the new explanation. The app still sent two unsupported requests to answer handlers. Two map commands yielded legend answers, and one command selected the compact UAE wetland view when its scope called for the offshore review view. A correct answer route sometimes omitted an expressly requested detail, such as viewing geometry or the meaning of a transparent change overlay.

[Machine-readable evaluation and release hashes](novice-standalone-r3.json)

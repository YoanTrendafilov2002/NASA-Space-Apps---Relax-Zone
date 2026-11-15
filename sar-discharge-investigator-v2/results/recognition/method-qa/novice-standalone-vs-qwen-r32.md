# Sealed R3.2 standalone and Qwen comparison

All 111 staged files matched the sealed `sartainty-agent-r3.2.zip`. Each mode saw the same 55 locked prompts and recorded map contexts once. The Qwen-enabled run used local `qwen3.5:4b` on 27 prompts, with all 27 inference calls completed. Both modes used the app's source-backed answer handlers and made zero catalogue-provider lookups.

| Measure | Standalone | Qwen-enabled |
| --- | ---: | ---: |
| Expected action | 39 / 55 | 48 / 55 |
| Expected action and answer kind | 33 / 55 | 41 / 55 |
| Complete scoped reply | 26 / 55 | 33 / 55 |
| Complete question reply | 14 / 38 | 24 / 38 |
| Correctly scoped map command | 6 / 9 | 5 / 9 |
| Unsupported request clarified | 6 / 8 | 4 / 8 |

Qwen improved 10 scoped replies and regressed three. The number of unsupported requests sent to answer handlers increased from two to four. Every unsupported request either received a clarification or entered an answer handler. The 55 cases are an authored challenge with fixed expectations. The prompts stay outside training and calibration.

[Aggregate results and archive hash](novice-standalone-vs-qwen-r32.json)

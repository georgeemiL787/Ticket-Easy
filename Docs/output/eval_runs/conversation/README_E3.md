# E3 conversation service, rules only (no AI model), 16 October 2026

Run with TEAM_B_LLM=none, PYTHONUTF8=1 (console encoding only), commit 4944d18 + the usage log changes.

| Measure | Result | File |
|---|---|---|
| NLU intent accuracy (155 labelled messages) | 87.7% (136/155); baseline 87.42% (151 messages), so within the allowed -0.02 | nlu_rules.txt |
| 240 conversations: intent / decision / citation | 62.5% / 66.2% / 36.0% (all styles) | orchestrator/reports/eval_presentation_rules_2026-10-16.json, .md |
| locale match | 98.9% | same |
| handoff precision / recall | 74.2% / 82.1% | same |
| solved without a person | 205 of 212 (85.4%) | same |
| wrong_or_unapproved_actions | 0 of 0 writes | same |
| unbacked_facts | 0 | same |
| scenarios | 83 active (shop_001 67, noon_eg 16), 83 passed, 0 pending, 0 failing | scenarios.txt |
| comparison with phase0 baseline | no difference on any measure | compare_rules_vs_phase0.txt |

Per style (intent): en 76.7%, ar 66.7%, mixed 66.7%, arabizi 40.0% (appendix only).

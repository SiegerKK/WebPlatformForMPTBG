# NPC Brain v3 — PR 4: вопросы к реализации

После изучения документов:

- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_consolidated.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_final_decision_chain_examples.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr1_final_sleep_and_survival_fixes.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr1_review_after_recheck.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr2_questions.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr2_questions_answer.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr2_revised_needs_liquidity_contract.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr3_memory_belief_contract.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr3_closing_fixes.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr4_objectives_contract.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pre_pr5_hunt_prerequisites.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr5_active_plan_contract.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_pr5_future_e2e_tests_and_kill_target_logic.md`
- `/home/runner/work/WebPlatformForMPTBG/WebPlatformForMPTBG/docs/npc_brain_v3_post_pr5_kill_stalker_operation.md`

## Вопросы

Блокирующих вопросов к реализации PR 4 нет.

Реализация PR 4 и PR4-части из `npc_brain_v3_pre_pr5_hunt_prerequisites.md` может быть начата сразу.


## Явная фиксация границы PR 4 по hunt-objectives

В PR 4 hunt-objectives реализуются только как **reserved/prepared слой** для objective-generation/scoring/trace:

- `LOCATE_TARGET`
- `PREPARE_FOR_HUNT`
- `TRACK_TARGET`
- `INTERCEPT_TARGET`
- `AMBUSH_TARGET`
- `ENGAGE_TARGET`
- `CONFIRM_KILL`
- `RETREAT_FROM_TARGET`
- `RECOVER_AFTER_COMBAT`

Полноценная системная охота (`kill_stalker` operation с декомпозицией, tracking/repair lifecycle, execution semantics) остаётся **post-PR5** и реализуется по документу `npc_brain_v3_post_pr5_kill_stalker_operation.md`.

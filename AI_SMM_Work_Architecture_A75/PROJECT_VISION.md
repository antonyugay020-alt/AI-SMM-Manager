# AI SMM Manager — стратегия продукта (обязательный ориентир)

Статус: утверждено пользователем, 17 октября 2026. Этот документ — источник требований для последующих изменений. Не заменять его узкими планами по отдельным функциям.

## Правило №1 — контроль перед каждой выдачей

Перед каждой выдачей файлов обязательно проверить код, структуру, архитектуру и все взаимодействия компонентов. Это относится и к NOT_RELEASE-кандидатам. Известный дефект блокирует выдачу; компиляция и unit-тесты не заменяют проверку связей, отказов, восстановления, интерфейса, хранения и распакованного комплекта. Полные обязательные направления и границы подтверждения закреплены в AGENTS.md и RELEASE_POLICY.md. Общие архитектурные решения обязательны, подгонка под отдельный сценарий запрещена.

## Цель
Создать цельное, универсальное, производительное Windows-приложение AI SMM Manager: управление проектами, стратегии, планы и блок-схемы, контент-календари, задачи, аналитика, создание и обработка фото и видео, единый AI-менеджер. Продукт, а не демонстрация отдельных функций.

## Неизменяемые правила
0. **Архитектура важнее прохождения теста.** Исправлять причину повторяющихся сбоев в общем контракте, границе ответственности, управлении состоянием или конвейере качества. Тесты служат доказательством поведения продукта, а не целью сами по себе. Запрещено подгонять приложение под один acceptance-сценарий, ослаблять критерии качества или добавлять частные исключения вместо общего решения. Для каждого исправления указывать первопричину и проверять соседние сценарии, включая отрицательные контрольные случаи.
1. Каждая работа оценивается по влиянию на приложение в целом: архитектура, производительность, качество, UX, надёжность, совместимость и отсутствие регрессий.
2. Универсальные решения предпочтительнее частных патчей. Не подменять задачу ускорения всей системы ускорением одной операции (например, замены фона).
3. Выбранный проект задаёт контекст ВСЕЙ программы: стратегии, планы, календарь, задачи, медиа, аналитика, история и AI-диалог. Данные проектов не смешиваются.
4. Постоянная левая навигация и максимальный центральный рабочий стол. Каждый раздел открывает собственную полноценную рабочую среду. AI-панель справа, сворачиваемая. Не использовать маленькое универсальное текстовое поле для всех видов материалов.
5. AI создаёт структурированные результаты с содержательными заголовками, сохраняет в верный раздел, открывает результат на рабочем столе. Существующие материалы можно открыть, изменить, переименовать и удалить с подтверждением.
6. Стратегии и планы имеют текстовое и визуальное представление; контент-планы — календарь/таблицу; задачи — доску/список; фото/видео — соответствующие инструменты.
7. Редактировать только выбранный исходник. Эталон никогда не изменять, не отправлять модели и даже не учитывать без явного упоминания пользователем. Исходники не перезаписывать; результаты версионировать.
8. Ускорять ВСЮ программу, включая запуск, навигацию, AI-менеджер, фото- и видеопроцессы. Актуальная конфигурация пользователя: RTX 2080 SUPER 8 ГБ, Windows, Python/PySide6, Ollama qwen3:8b. Оптимизировать под доступные ресурсы без неоправданной потери качества; обрабатывать OOM и сохранять состояние.
9. Не выдавать непроверенную функцию за готовую. Разделять: реализовано, проверено статически, проверено на Windows/GPU, ещё не готово.
10. Новая сборка должна сохранять работающие функции и пользовательские данные; миграции и удаление — безопасные, с резервными копиями и подтверждением.

## Обязательная проверка перед каждой сборкой
- Как изменение приближает целостный продукт, а не одну частную функцию?
- Не нарушает ли проектную изоляцию и правильное размещение документов?
- Не нарушает ли правило исходник/эталон и сохранность данных?
- Ускоряет ли систему или хотя бы не создаёт новых блокировок интерфейса?
- Сохранены ли прежние функции? Что реально протестировано?
- Понятно ли пользователю, что изменилось, что осталось и что делать дальше?

Если изменение не проходит проверку — переработать его до выпуска, а не объявлять завершённым.


## Файловая архитектура (Stage 37)
Единое проектное хранение исходников, редактируемых документов, экспортов PDF и медиа. Выбор базовой папки для новых проектов. Экспорты не подменяют редактируемые оригиналы. Встраивание графиков и схем в PDF и перенос существующих проектов — незавершённые требования.

## Stage 38: измерение производительности
Никаких заявлений об ускорении без измерений. Локальная телеметрия не включает содержимое проектов.

## Stage 69: критерий готовности — ВСЁ содержание задания (18 октября 2026)
Критерий результата — не число 39/39 и не успешное выполнение одного генератора. Приложение должно выполнять полный перечень созданий и изменений, явно запрошенных пользователем, с контролем профессионального качества, соответствия контексту проекта и безопасным сохранением **каждого вида материала**. Стратегии, документы, тексты, сценарии, фактические изображения, видео, исходники, рабочие файлы и задачи — разные результаты; сценарий медиа не равен созданному медиафайлу. Любое неподтверждённое содержание и неполная работа должны явно отражаться в отчёте. Число публикаций и период берутся из задания; примеры на 39 публикаций не становятся системными нормами.


## Эволюция Stage 75 — контроль архитектурного подхода (19 октября 2026)
Качество **всей системы** оценивается до выпуска каждого изменения. Исправление единичного тестового слота/отрасли не считается релизом само по себе. План, исполнение, контроль, восстановление и сохранение должны иметь программные контракты и проверяемые инварианты. Полноценный автономный диалог — стратегическая цель, а не замена сегодняшнему приоритету целостного производственного конвейера. Если разрозненный баг открывает дефект уровня управления заданием, сперва исправляется общий механизм. В документации честно перечислять, что реально проверено и что не проверено.


## Уточнение владельца: полный цикл и вход на любом этапе (19 октября 2026)
Менеджер принимает новый или существующий проект на любом этапе: идея, стратегия,
производство и обработка контента, подготовка и размещение публикаций, последующее ведение.
Разовые задания (отдельный план, текст, обработка медиа и другие работы) равноправны
полному ведению и не должны запускать ненужный полный цикл. Контекст проекта и принятые
материалы используются без повторного запроса к пользователю. Просмотр завершённого
проекта не запускает выполнение. Новое поручение имеет отдельный жизненный цикл.

Полномочия на внешние публикации задаются явно; необходимы подтверждение результата
размещения и защита от дубликатов после сетевого сбоя. Это требования к разработке,
а не заявление о реализованной публикации.

Модели и специализированные инструменты должны быть заменяемыми по ролям. Автор и
проверяющий получают отдельные контексты, но работают с одной версией требований и фактов.
Та же модель в двух ролях не считается полностью независимым источником суждения.
Другие модели подключаются после сравнительных испытаний; внешняя передача материалов
и платные сервисы требуют явного разрешения. Не загружать все модели в VRAM одновременно
без измерения доступных ресурсов. Stage 76 не создан; эта кодовая линия остаётся на базе V9 / Stage 75. Текущий рабочий пакет — отдельный тестовый кандидат, не релиз.

## Актуальное уточнение происхождения исходников — 23 октября 2026

В нескольких исторических заметках Stage 76 ошибочно назван находящимся в разработке. Пользователь уточнил, что Stage 76 не создан; исходники этой стадии в рабочей линии отсутствуют. Активная линия здесь — Stage 75 / V9 с рабочими кандидатами A69–A75. Старые отчёты сохраняются как история проверок, но не являются кодом Stage 76.


---

# A52 architecture review — 22 October 2026

## Source lineage and latest Windows evidence

A52 continues the Stage 75 / V9 source base. Stage 76 had not been created; earlier references to it as "under development" were inaccurate. A51's Windows result (2026-10-22 17:05:33) matched its source fingerprint and passed package integrity, 454 unit tests, Qt smoke, Ollama protocol/schema checks, and evidence integrity. It failed the live backend content pipeline, independent semantic acceptance, and live GUI full-workflow gate. The three tested briefs all failed at least one content/document acceptance condition; the clothing-alteration GUI workflow persisted drafts/checkpoints but correctly stayed pending. This evidence is not a release approval and the fixed synthetic cases do not establish universal performance.

## Architectural cause

The production path had two different factual-acceptance boundaries. A generated post could be marked `semantic_review_passed` and persisted in the resumable checkpoint after a per-post semantic edit plus deterministic `editorial_risks`. The final `call_editor(require_inventory=True)` then ran its own claim inventory and source adjudication across the whole strategy/calendar. A51 showed this second path finding unsupported operational/process/outcome claims that the per-post path had accepted. The duplicated path allowed a state transition to `accepted` before the source-backed claim contract used at final delivery had examined that artifact.

The report also shows qwen sometimes selecting a topically related source and calling it support, while the host appropriately rejected the weak lexical match. Relaxing that check would weaken factual safety. The unresolved product problem is therefore generation and review reliability: the writer continues to invent process details or an unconfirmed offer despite repeated correction, and a conservative gate blocks completion. The result should remain blocked until content is safe and complete.

## A52 system change

A52 connects publication acceptance to the existing canonical `call_editor` path through `review_publication_evidence`. Before a publication enters the production checkpoint, that same source ledger, full claim inventory, constrained source citation and host adjudication contract examines the publication artifact. The final whole-document review remains in place for cross-document consistency, completeness and task-wide delivery. The production entry injects the reviewer as a dependency; if the production acceptance boundary is enabled but the reviewer is absent or unavailable, the publication is not accepted. Low-level drafting tests remain able to exercise the executor without pretending they completed production acceptance.

This addresses the acceptance-boundary inconsistency. It does not improve qwen's content quality by itself, prove the factual validator is correct for every domain, or make the current Windows workflow pass. It adds at least one additional Ollama review per publication, so latency and memory behavior on the user's RTX 2080 SUPER must be measured. No factual threshold was relaxed.

## Local evidence

- 457 developer unit tests pass on the available Linux/Python environment.
- New regression checks verify that one publication is sent through the same inventory/citation contract and that the production pipeline injects that reviewer before checkpoint acceptance.
- Windows, live PySide6 GUI, Ollama qwen3:8b generation/recovery, and complete GUI behavior have not been run in this environment.
- Candidate package status remains `NOT_RELEASE` until fresh Windows validation, live workflow, and cross-task quality/recovery checks pass.

## Next system work

Work A75's multi-model update gives standalone text four distinct reviewer models, one per review dimension. R38 routes standalone task dispatch through the shared provider transport and extends randomized acceptance to both standalone writing and compound strategy/calendar workflows. When OpenAI comparison is enabled, R39 requires a random local Ollama author alongside the API model. R40 makes the compound planner own platform format and schedule fields, preserves individually valid topics when a batch contains duplicates, asks only for missing slots, normalizes common JSON envelopes, and adds source-language fidelity to independent editorial checks. These are reusable contracts across models and tasks; they do not establish universal content quality. Fresh Windows/Ollama/API validation remains outstanding. R41 fixes contradictory randomized standalone instructions, strengthens the general content usefulness/repair contract, and retries incomplete semantic-inventory coverage by splitting batches. R40 passed integrity, unit tests and GUI smoke on Windows, but its live content acceptance remained blocked; R41 needs fresh Windows/Ollama/API acceptance.

## A75 multi-model editorial panel — 23 October 2026

Standalone generated text is reviewed by four random, distinct model families selected from installed Ollama models for each pass. The qwen3 family (author/repair model) is excluded. A repaired draft receives newly randomized reviewers, with the previous model excluded for each same review dimension. `quality_review_model_pool.json` supports an optional allowlist/exclude list; a pool with fewer than four usable reviewers fails closed. Calls run sequentially with `keep_alive=0` for the user's 8 GB GPU profile. Local model tests cannot verify installed model quality. Compound strategy/calendar artifacts still use their existing review path, so this change does not establish one universal multi-model pipeline or universal content quality. A75 remains NOT RELEASE pending fresh Windows/Ollama and broad workflow validation.


# A52 Windows run review — 22 October 2026

The user-provided report matched A52 fingerprint `3ed10f20109d1df2ac373629077ddc110b4b66583920216eb37fb9a636cde166`. Integrity, Windows, 457 unit tests, Qt smoke, Ollama protocol probes, independent semantic acceptance, and evidence integrity passed. Live backend acceptance timed out after 2,400 seconds; the GUI workflow remained blocked with no documents persisted.

## A53 architecture correction

The A52 production path ran the canonical full claim inventory and source adjudication for each publication and again for the complete strategy/calendar. That repeated whole-artifact work scaled with publication count and contributed unnecessary model calls. A53 removes this duplicate route: each publication remains subject to local structure, content, semantic, and deterministic factual checks and is stored as a recoverable candidate checkpoint. The production boundary performs the complete source-backed inventory once against the assembled strategy and calendar. The result is still blocked unless that independent review passes.

Saved rejected strategies now stop their repair loop when the same validation finding remains after a targeted repair. This is a general no-progress rule; it does not waive validation or replace saved user work.

A53 has only local automated validation so far. The A52 timeout is historical evidence, not proof A53 completes; target Windows/Ollama acceptance remains required.


# A53 Windows validation review — 23 October 2026

The user-provided `VALIDATION_RESULTS(20261023-063335).zip` matched A53 fingerprint `9cb876638d7e8095554b347a8910bef7815c6048dec80a8383e3cb5e04669df3`. Windows, package integrity, 456 unit tests, Qt smoke, Ollama basic/schema calls and evidence integrity passed. One of three complete backend cases passed; the other two, independent semantic acceptance and full GUI workflow failed. The prior 40-minute timeout was eliminated in this run, but acceptance quality was not achieved. The original evidence archive is retained under `history/A53_WINDOWS_20261023_063335/` in the developer kit.

## A54 Windows validation review

The A54 user report matched fingerprint `f7d978f784c4852eddeb7b58ebf462dbec3202b7083f2c69f28b0ace1b526163`. Package integrity, Windows, 458 unit tests, Qt smoke, Ollama protocol/schema checks, and evidence integrity passed. The live backend content pipeline, independent semantic acceptance, and GUI full workflow failed. Three synthetic briefs produced unsupported process, material, quality, safety, or outcome claims; strategy recovery also failed one case. The GUI workflow remained pending and persisted no complete delivery. The report is retained in the developer kit under `history/A54_WINDOWS_20261023_072319/`.

## A55 architecture changes

- Source review routes narrative goals and plan recommendations using their section and field roles, rather than treating them as facts about the business.
- Citation-fit checks distinguish simple fact paraphrases from operational processes, commercial conditions, and promised outcomes. Outcome language still needs direct source support.
- The shared generation policy explicitly separates verified public claims, proposed visuals, and questions requiring owner confirmation.

These changes have local regression coverage. Windows/PySide6/Ollama acceptance has not run for A55; do not treat local tests as release evidence.

## A55 Windows findings and A56 response

A55 Windows testing passed packaging, 461 tests, Qt smoke, and Ollama protocol/schema checks. Its live content pipeline, independent semantic acceptance, and GUI full workflow failed. The report showed that the semantic editor demanded concrete real actions even when process facts were unknown, that publication writers received generated strategy text as prompt input, and that a generic deliverable schema sentence could be treated as a business fact. A56 aligns the quality rubric with verified information, removes generated strategy text from publication writing/repair prompts, recognizes generic deliverable-field lists as planning metadata, and prohibits inferred business categories. It retains strict factual blocking and immutable accepted checkpoints. A56 still requires the Windows/PySide6/Ollama validation before release.

# A58 Windows evidence and A59 review architecture — 23 October 2026

A58's source-matched Windows report passed package integrity, PySide6 GUI smoke, Ollama protocol/schema checks and 472 unit tests, but failed live content acceptance, independent semantic acceptance and full GUI delivery. Workshop output included unsupported quality/professional positioning; language-school output included unsupported efficiency/confidence claims; ceramics output contained unconfirmed commitments about answering questions and operational steps. The GUI kept incomplete work pending. The report and full traces are retained in the developer kit history.

A59 consolidates the final editorial verdict and full business-claim inventory into one host-indexed review transaction. Each segment is reviewed once for editorial issues and inventory coverage; exact host evidence IDs are validated before the separate source adjudicator checks business claims against the current request and verified project materials. Duplicate exact findings are resolved once. This removes the competing claim-inventory classifier without weakening source requirements. The factual generator still needs live Windows/Ollama validation across varied briefs; A59 is not a release.

# A59 Windows validation and A60 acceptance redesign — 23 October 2026

The A59 report matched its fingerprint. Package integrity, Windows detection, 471 unit tests, live Qt smoke, Ollama protocol/schema checks, and evidence integrity passed. Live backend acceptance, independent semantic acceptance, and full GUI workflow failed. The report and traces are preserved in the developer kit. In the workshop trace, a whole-document transaction classified all 86 evidence segments as business claims; editorial planning fields and actual business assertions were therefore mixed before source adjudication. Other cases also had incomplete review coverage. The source authority check appropriately refused weak topical overlap.

A60 gives editorial review, business-assertion classification, and source resolution separate contracts. Assertion classification consumes immutable typed segments in bounded batches and uses a constrained host-keyed object requiring exactly one decision for every evidence ID. It identifies actual-business assertions without deciding whether they are true; the independent source ledger remains responsible for authorization. Failure to cover any segment blocks acceptance. No factual threshold or completion requirement was relaxed.

The source-matched A60 Windows run passed package integrity, its unit suite, Qt smoke, Ollama protocol/schema probes, checkpoint recovery, and evidence integrity. Live pipeline and independent semantic acceptance passed only for ceramics; workshop and language-school outputs were blocked by unsupported business claims. The full GUI task stayed pending with only the strategy delivered because that same final-review rejection was not being handed back to the strategy draft repair path. A60 is not a release.

# A61 final-review recovery handoff — 23 October 2026

The A60 trace exposed a lifecycle boundary defect: the complete-delivery reviewer could reject an unapproved strategy after the strategy-only reviewer had marked its checkpoint valid, but `final_review_repair` did not attach those final-review findings to the persisted strategy draft. Recovery therefore reused the rejected text as a valid candidate and repeated the same final rejection. A61 routes strategy findings from the final verdict into the exact unapproved draft's repair feedback, retaining its bytes and checkpoint until a repaired candidate is independently revalidated. Already-saved strategy documents remain immutable and outside this repair path.

A61 has 473 local developer tests passing on Python 3.12, including a regression that checks the full-review-to-draft handoff and confirms the original checkpoint remains untouched before a repair is accepted. This does not establish live Windows/Ollama or end-to-end quality. A61 remains a test candidate pending another source-matched Windows run across all three briefs and the full GUI lifecycle.

# A61 Windows findings and A62 evidence-preserving repair flow — 23 October 2026

The A61 report matched its fingerprint. Windows, package integrity, its unit suite, Qt smoke, Ollama protocol/schema checks, and checkpoint recovery passed. Workshop and ceramics pipelines passed, but the language-school brief was blocked by an unsupported comfort statement. The live GUI also remained pending: the full review correctly cited unsupported publication claims, yet a final-review slot repair still repeated one of them.

Trace review found a second state-boundary defect. The final reviewer supplied the exact claim, source segment ID, field, and section, and the production pipeline mapped the finding back to its publication slot. `execute_compound` then reduced the structured finding to its reason string before saving the repair checkpoint. The publication writer therefore received no exact rejected wording to target. A62 preserves the full host-validated finding in the draft checkpoint, renders its precise claim and provenance into repair feedback, and uses that same formatter for field edits, full publication repairs, and fallback generation. All repaired output still passes the existing independent gates before acceptance.

A62 adds lifecycle coverage for both persistence of the structured finding and delivery of its exact claim and evidence ID to the writer. A fresh Windows/PySide6/Ollama run is required; this candidate is not a release.


# A62 validation finding and A63 lifecycle correction — 23 October 2026

The A62 Windows report matched fingerprint `1aee3abe6ee2e5d32c643613abd7a0c5d670b7b448908c7d901ab9be42390d36`. It passed 474 unit tests, the Qt smoke test, Ollama protocol/schema checks, and the full GUI workflow. The live backend and independent semantic checks failed on the workshop brief. The trace showed that a calendar-only repair carried the unapproved strategy forward; a later whole-delivery review found a strategy claim, but the executor treated that in-flight draft as immutable and reused it unchanged. A63 makes immutability depend on explicit accepted status (`already_saved: true`); carried-forward unapproved work stays repairable. A regression test covers both accepted and unapproved documents. A63 requires fresh Windows/Ollama validation and is not a release.


# A64 canonical channel contract — 23 October 2026

A63 artifact review showed that a channel explicitly present in the original request was not authoritative across the complete analysis-to-calendar pipeline, despite the reported synthetic acceptance passing. A64 introduces one canonical channel representation, derives explicit channel requirements at the orchestration boundary from the original request, carries explicit/recommendation provenance through strategy and calendar planning, validates checkpoint compatibility, and labels model recommendations in output. A single calendar currently accepts one channel; multi-channel requests stop for clarification instead of silently mixing platform formats. This is a data-contract correction spanning the pipeline, not a per-brief rule. It requires fresh Windows/PySide6/Ollama acceptance and remains a test candidate.


# A65 artifact ownership and completion boundary — 23 October 2026

A64 Windows traces showed that strategy-only review could block a compound task for publication-level fields before its separate calendar was generated. A65 adds a shared deliverable ownership rule: publication-scoped requirements are deferred at the strategy-only stage and checked against the complete strategy/calendar bundle; explicit strategy allocation remains enforced. It also removes unsupported category placeholders from unaccepted drafts and shares grammatical quantity rendering for Russian calendar summaries. A64 still had live content and semantic-quality failures; A65 requires fresh Windows/Ollama verification and remains NOT RELEASE.


R42 keeps the randomized fast acceptance benchmark to one generated attempt, so it reports the selected model's first-pass quality without spending the remaining case budget replaying the same task. Its generic reviewer compares specificity to the facts actually provided; incomplete semantic JSON/schema/timeouts can trigger bounded batch splitting. The R41 Windows fast run passed integrity, GUI smoke and 648 unit tests, but both live cases failed and OpenAI API acceptance was disabled. R42 has 649 local tests passing and still needs fresh Windows/Ollama/API validation.

# Agent Rules — VoiceStudio

Binding for every AI agent (Claude, Codex, Cursor, review bots, …). CLAUDE.md is the full constitution; this is the operating contract. When they conflict, CLAUDE.md wins.

## Token economy (owner directive, 2026-07-20; tightened 2026-07-28)
- **Default to the shortest response that fully answers.** Outlines and tables over prose; no preamble, no recap of what you just did, no re-explaining a fix the diff already shows. Applies to every response, not just status updates.
- Lead with the outcome. No narration, no restating diffs, no filler praise, no plans you're about to execute anyway.
- Status updates: one line. Final reports: only what changes the reader's next action.
- Don't re-derive what CI, linters, or review bots already computed — read their output first (`gh pr checks`, bot comments via `gh api .../pulls/N/comments`).
- Mechanical rules live in deterministic tests, never in agent effort: changelog style (`tests/test_changelog_style.py`), locale parity (`tests/test_locale_parity.py`), version lockstep (`tests/test_app_version.py`), CJK (`tests/test_no_hardcoded_cjk.py`).
- Run targeted tests while iterating; full suites only before landing.
- Tests and CI simulate CI honestly: `HF_HUB_OFFLINE=1` + empty `HF_HUB_CACHE` — a populated dev cache masks real failures.

## Cross-platform parity: behaviour, not performance
- The parity rule covers user-visible BEHAVIOUR. Hardware acceleration varies by host by design (CUDA/MPS/DirectML, Triton availability, `torch.compile`); skipping an optimization where it physically cannot work is not a parity violation.
- Do not "fix" a parity finding by disabling a working optimization everywhere. That trades a real regression for a semantic one.
- A feature the user can see and use on one OS but not another IS a violation. Judge by what the user can do, not by how fast it runs.

## Merge protocol (hard rules)
1. Never merge without review. Harvest CodeRabbit + Greptile comments first; never merge with an unread Critical/P1.
2. Never accept a PR as-is: fix findings ON the PR branch pre-merge (maintainer commits fine; credit contributors in CHANGELOG). No merge-then-fix, no comment-and-walk-away.
3. Merge current `main` into stale branches before judging their CI — PR-green under an old workflow ≠ main-green.
4. Gate: "Tests (backend + frontend)" green + MERGEABLE.
5. After EVERY merge: watch `main`'s own post-merge runs to green (`gh run list --branch main`). Red main = drop everything and fix.

## Change rules (see CLAUDE.md for full text)
- Root-cause the class, not the instance; fail-before/pass-after regression test; smallest correct change.
- Default behavior identical on macOS/Windows/Linux; platform-only features go behind explicit opt-in. Divergent default = P0.
- Local-first: no new required network calls; any HF download gated on installed-ness or explicit user action; all synthetic audio through the `mark_synthetic` chokepoint.
- Every user-facing string via i18n, present in ALL 21 `frontend/src/i18n/locales/*.json` with real translations.
- Docs-sync in the same PR. CHANGELOG Unreleased: quiet one-liners ending `(#N)` + `— thanks @user!` for community work, under a short `**Highlights**` list.
- Versioning: `frontend/package.json` is the single source of truth; never bump without the owner asking.
- `frontend/package.json` dep changes require regenerating root `pnpm-lock.yaml` (Nimi builds use `--frozen-lockfile`).
- Issues: absorb or decline — never defer to a future version. Check the open-PR queue before implementing community-reported fixes.

## Shared select controls

- Use `frontend/src/components/SearchableSelect.jsx` for all new or redesigned select boxes. Reuse `VoiceSelector` for voice choices. Do not introduce native `<select>` controls.
- Provide a localized `ariaLabel`; use `menuPortal` inside scrolling or clipping containers. Preserve keyboard selection and disabled states.

## Agent skills

Project development skills are pinned in `skills-lock.json` and installed under
`.agents/skills/`: Vite and FastAPI.
Repository rules and tracker mappings override generic skill guidance.

### Issue tracker

GitHub Issues on `debpalash/VoiceStudio`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

<!-- nimicoding:managed:agents:start -->
# Nimi Coding Managed Block

- From the repository root, invoke the pinned project-local CLI as `pnpm exec nimicoding`; do not probe or rely on a global `nimicoding` binary in `PATH`.
- Product authority lives under `.nimi/spec/**`.
- Choose authority and code queries when their declared scope can resolve an uncertainty that affects the current task; reuse sufficient current evidence. Query scope is not the limit of host reasoning or authorized work, and hypotheses are not product authority.
- For canonical authority authoring, read only `.nimi/methodology/authority-authoring.yaml`, the affected authority files or bounded task context, and CLI diagnostics.
- Use `pnpm exec nimicoding authority context <path> <id> --max-units <n> --max-bytes <n> --json` only for the complete declared outgoing interpretation closure; it is not complete task context, and failure never permits guessed or partial context.
- Use `pnpm exec nimicoding authority diff` and `pnpm exec nimicoding authority impact` with explicit `--max-bytes`; impact reports declared review obligations and does not prove implementation, consumers, or tests are synchronized.
- Use `pnpm exec nimicoding authority change-candidates` only with explicit channels and budgets; its complete union is recall input, never conflict, retirement, absence, authority, or conformance judgment.
- When explicit authority links are needed, use `pnpm exec nimicoding code authority --repo <root> --authority <id> --max-files <n> --max-bytes <n>` to locate annotated code, and use `--source <path>` for code-to-authority lookup. Results cover only explicit markers and authority lifecycle; they do not prove implementation conformance or evaluate unannotated code.
- For a new or changed authority-governed feature, add the reserved standalone physical line `// @nimi-authority: <exact-id>` in TypeScript/TSX, Go, or Rust, and `# @nimi-authority: <exact-id>` in Python. The scanner does not prove language comment context, so use this reserved form only for intentional links at a few key semantic owners.
- Use `// @nimi-deprecated: <exact-id>`, or `# @nimi-deprecated: <exact-id>` in Python, only after direct authority evidence or a real product failure confirms obsolete semantics; find it with `pnpm exec nimicoding code authority --repo <root> --audit --max-files <n> --max-bytes <n>` and remove it with the hard cut.
- When a selected TypeScript or TSX consumer still has a static-dependency question, use `pnpm exec nimicoding code context <path> --repo <root> --symbol <identifier> --tsconfig <path> --max-bytes <n>` for bounded root-direct static dependencies; it is not inbound impact, runtime dispatch, or complete task context.
- Use `pnpm exec nimicoding sync --check` to diagnose drift in package-owned managed projections, `pnpm exec nimicoding sync --apply` to restore them, and `pnpm exec nimicoding doctor` to diagnose package/managed compatibility. These commands do not validate product authority, implementation conformance, or task readiness.
- Under `.nimi/spec/**`, author only closed multi-unit `*.authority.yaml` containers or single-unit `*.authority.md`; historical document formats are unsupported and never inferred.
- Run `pnpm exec nimicoding authority fmt` on each changed file, then `pnpm exec nimicoding authority check` on the complete authority input set.
- A failed project-local `pnpm exec nimicoding ...` invocation supplies no usable result. Pause decisions that require refused, missing, or incomplete results; continue independent authorized work. Never substitute guessed, corpus-wide, or fallback context, or treat diagnostics or partial output as complete context; choose repair values only from product/task authority.
- Keep derived and local verification output under `.nimi/local/**`; it is never product authority.
<!-- nimicoding:managed:agents:end -->

<!-- nimi-app:managed:start -->
## Nimi App development

- For auditing adaptation feasibility or cost, creating, adapting, upgrading or releasing this App, read [the lifecycle skill](.agents/skills/nimi-app-lifecycle/SKILL.md) and only the relevant scenario. Audit-only requests do not begin implementation; prior reports remain evidence-dependent.
- For first integration or foundation wiring changes, use the selected app-tools package's generated reference and reuse its Host, preload, renderer, session, App Access, AIConfig and AI call wiring. Follow the adaptation guide's baseline checks and reuse passing results for ordinary business edits.
- Keep App-owned product behavior, Host code, business accounts and non-AI services with this repository. Use the SDK/Kit Local App carrier for Nimi AI, configuration, storage and session access.
- Development may consume complete local npm tarballs through the supported explicit overrides and version matrix; public-release preflight uses registry resolutions. Do not substitute source-workspace or directory links.
- App Tools owns its lifecycle skill, this block, managed workflow and declared engineering fields; preserve other instructions, product source and licenses. Existing adoption does not create fresh scaffold intent or lock.
- Product-operation guides apply to their specific business tasks; they do not replace the Nimi development boundary.
- Reuse the user's confirmed scope and authorization. Report command checks separately from actual App journeys; unrun relevant paths remain NOT-VERIFIED.
<!-- nimi-app:managed:end -->

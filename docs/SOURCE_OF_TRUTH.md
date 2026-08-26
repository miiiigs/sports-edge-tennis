# Source of Truth

This document records the canonical provenance of the requirements imported into the
dedicated Sports Edge tennis repository.

## Source repository

| Field | Value |
| --- | --- |
| Repository | `sports-edge-ai` |
| Remote | `https://github.com/miiiigs/sports-edge-ai.git` |
| Source ref | `origin/main` |
| Source commit | `f379088d31888b29688a3905f5f07c3dbac30e60` |
| Import timestamp (UTC) | `2026-08-23T20:15:44Z` |

The source football repository was fetched read-only. No files in the football repository
were modified during this import.

## Spec authority precedence

The committed project governance (`specs/authority/NEW_SPEC_AUTHORITY_ORDER_2026-08-15.md`)
establishes PDF-first authority:

1. Direct user instructions in the current task.
2. Latest client PDFs supplied on August 15, 2026:
   - `initial_data/01-tennis-build-spec.pdf`
   - `initial_data/03-thirty-day-plan.pdf`
3. Matching Markdown files only where they do not conflict with the PDFs.
4. Reset / migration guidance in the pasted handoff note.
5. Earlier repository implementation, older handoff docs, and historical experiments.

Working rule: if a PDF and a Markdown file disagree, follow the PDF and record the
conflict explicitly. Do not silently merge contradictory requirements.

## Edge Desk realignment authority review

T-ED0 was re-verified on Wednesday, August 26, 2026 against the locally available
non-repository artifacts that could plausibly qualify as Edge Desk design authority.

Available local artifacts inspected:

- `C:\dev\sports-edge-ai\meeting_notes\Sports-Edge-Technical-Overview_1.pdf`
  - Explicitly Sports Edge-named.
  - High-level technical overview dated July 2026.
  - Useful operational context, but not an implementation contract or migration-ready
    design pack.
- `C:\dev\sports-edge-ai\initial_data\Sports-AI-Developer-Pack-V1(2).pdf`
  - Broader Sports AI Betting Research System V1 Developer Pack.
  - The only locally available developer-pack artifact covering run identity,
    lock/finalization, immutable ledger concepts, and policy versioning.
  - Not tennis-specific and not literally named an Edge Desk design pack.

Authority conclusion:

- No separate locally available file was found that is both explicitly Edge Desk and clearly
  the controlling implementation spec for Tennis T-ED0 / T-ED1.
- The Tennis build specification therefore remains the governing source for tennis-specific
  mathematics, especially the Manager consensus rules in `7.1 Consensus checks`.
- The broader Sports AI Developer Pack may be used only as additive design guidance for run
  identity, immutable lineage, and policy-history concepts when it does not contradict the
  dedicated Tennis specification.
- The Edge Desk technical overview is contextual evidence only.

Confirmed cross-source caution:

- The broader `Sports-AI-Developer-Pack-V1(2).pdf` explicitly promotes a learned
  meta-model / "no equal voting" architecture, while the dedicated Tennis build
  specification requires the C1-C4 active-agent consensus contract.
- For this repository, that broader pack does not supersede Tennis C1-C4 because no newer
  explicit tennis authority was found that directly replaces those rules.

## Canonical source paths and provenance

| Source path | Source blob SHA | Destination |
| --- | --- | --- |
| `initial_data/00-developer-brief.pdf` | `91b4e4db32c647503e71d3132f4ec7bd44a7bf8e` | `specs/00-developer-brief.pdf` |
| `initial_data/01-tennis-build-spec.pdf` | `176327cf3f6b5a8172e13dc59e0d39882502b54c` | `specs/01-tennis-build-spec.pdf` |
| `initial_data/03-thirty-day-plan.pdf` | `a69b54282bc16841a8626f5f99f37fc224b9c0b7` | `specs/03-thirty-day-plan.pdf` |
| `initial_data/tennis-build-spec.md` | `01254c0d42ce11a19ed2251fff9795dc00704812` | `specs/tennis-build-spec.md` |
| `initial_data/developer-setup-costs-and-30-day-plan.md` | `fb2d3e4473c2876447ad516152aa0b1f07e8df25` | `specs/developer-setup-costs-and-30-day-plan.md` |
| `research/spec_migration/NEW_SPEC_AUTHORITY_ORDER_2026-08-15.md` | `b8658e338c79cb16b607d9b403bbddca6f5f80db` | `specs/authority/NEW_SPEC_AUTHORITY_ORDER_2026-08-15.md` |
| `research/spec_migration/FULL_SPEC_ALIGNMENT_AUDIT_2026-08-17.md` | `a724f6679db274f28c74816b041e367db930e530` | `specs/authority/FULL_SPEC_ALIGNMENT_AUDIT_2026-08-17.md` |

SHA-256 checksums for all imported artifacts are recorded in `specs/SOURCE_MANIFEST.json`.

## Requirement citation rule

All future tennis model work MUST cite the canonical requirement section it implements
(including source artifact and section), so that every implementation decision is traceable
to a committed specification rather than to an assumption or a previous assistant report.

Previous ChatGPT/DeepSeek reports are contextual evidence only, never the requirement
source of truth.

## Authority verification note

The prior audit identified a numbering discrepancy between the PDF and Markdown 30-day
plans. The committed project governance resolves this through PDF-first authority. That
policy is present in the freshly fetched `origin/main` at the path above and is re-verified
for this import.

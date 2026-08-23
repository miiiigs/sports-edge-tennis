# New Spec Authority Order

Last updated: Saturday, August 15, 2026

## Purpose

Record the authoritative order for the revised August 15, 2026 client specifications and the
known conflicts between the newly supplied PDFs and the older Markdown/reference materials.

## Authority order

1. Direct user instructions in the current task.
2. Latest client PDFs supplied on August 15, 2026:
   - `initial_data/01-tennis-build-spec.pdf`
   - `initial_data/02-football-build-spec.pdf`
   - `initial_data/03-thirty-day-plan.pdf`
3. Matching Markdown files only where they do not conflict with the PDFs:
   - `initial_data/tennis-build-spec.md`
   - `initial_data/football-build-spec.md`
   - `initial_data/developer-setup-costs-and-30-day-plan.md`
4. Reset / migration guidance in the pasted handoff note:
   - `.codex/attachments/.../pasted-text.txt`
5. Earlier repo implementation, older handoff docs, and historical experiments.

## Working rule

If a PDF and a Markdown file disagree, follow the PDF and record the conflict explicitly.
Do not silently merge contradictory requirements.

## Confirmed conflicts and cautions

### 1. Missing upstream authority

The August 15 PDFs explicitly reference `00-developer-brief.md` and its rules `R1-R10`, but
that file is not present in the repo. Treat it as a blocker for any rule that appears to rely on
those omitted definitions.

### 2. Staking conflict

- `03-thirty-day-plan.pdf` specifies `quarter-Kelly` with a `2%` cap in the arithmetic library.
- The Markdown build specs currently describe `half-Kelly` with a `2%` cap.

Until clarified by the missing developer brief or a newer client instruction, this remains an
open specification conflict.

### 3. Football market-priority conflict

The football PDF is the governing source for market priority. It explicitly puts:

1. over/under total goals
2. total corners
3. both teams to score
4. Asian handicap
5. total shots / shots on target
6. `1X2`

Any older repo direction that treats `1X2` Poisson work as the main football path is now
baseline/research only unless re-authorized.

### 4. Architecture supersession

The new PDFs require football and tennis to be separate builds with:

- separate repositories
- separate databases
- no shared runtime

The current single-repo implementation is therefore transitional infrastructure, not the target
end-state architecture.

## Immediate operational consequence

All new planning, coding, and gap analysis should be written against the PDF-first authority
order above. Existing work may still be reused, but only after checking it against the revised
PDF requirements.

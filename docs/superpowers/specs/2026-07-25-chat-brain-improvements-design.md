# Chat & AI Brain Improvements — Design

Approved 2026-07-25. Approach A for model selection.

## Goals

1. Clearer multi-model council selection within model sets (min 1 / max 5).
2. Real chat file uploads (replace hardcoded mock attachments).
3. Voice: confirm only on Cancel discard; Stop continues without confirm.
4. Enter does not send; only Send submits. Enter inserts newline.
5. Fix Verdict treating UI content above the composer as the user prompt; current composer text is the only prompt; prior verdict is context only.
6. Composer textarea auto-grows with content (max height then scroll).
7. Remove Assessment Criteria from UI and turn workflow.
8. Soft-delete turns with unlimited Undo restore.
9. Seed Chafic El Khazen Brain persona; add Referee/Merger system council as default on open (not Balanced).

## Non-goals

- Ad-hoc model pick without model sets.
- Scraping / census work.

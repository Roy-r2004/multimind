# Chat & AI Brain Improvements Implementation Plan

> **For agentic workers:** Implement tasks in order. Prefer working software over ceremony.

**Goal:** Ship approved chat UX fixes, real uploads, soft-delete undo, Chafiq Brain persona, and Referee default council.

**Architecture:** Frontend chat composer/council UX changes; backend soft-delete + restore, attachment upload, verdict context fix, seed/default model set + brain persona.

## Tasks

### Task 1: Quick FE UX (Enter, grow, voice, criteria, council clarity)
- Files: `src/routes/chat.tsx`, `VoiceRecorderButton.tsx`, `CouncilPickerModal.tsx`, remove criteria components/usage
- Enter → newline only; auto-resize textarea; cancel confirm only; remove criteria; clearer multi-select in council picker

### Task 2: Real file upload
- BE: chat attachment upload endpoint + store bytes/metadata
- FE: file input → upload → attach names/ids into turn instructions or attachment refs

### Task 3: Verdict context fix
- BE: `_latest_previous_verdict_context` / prompts — do not treat prior UI/answers as current user question

### Task 4: Soft-delete + Undo
- BE: soft-delete columns + restore endpoint; list excludes deleted by default
- FE: Undo action after delete (no TTL)

### Task 5: Brain persona + Referee default council
- Seed system model set with Referee prompt; default selection slug
- Seed/update Chafic user brain with persona text
- FE: prefer referee set on load

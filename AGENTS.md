# CrowdTurb Agent Guide

This file applies to the whole repository unless a deeper `AGENTS.md` overrides it.

## Read first

1. `docs/requirements.md`, `docs/plan.md`, and `docs/backlog.md` for scope and owned work.
2. `contracts/README.md` plus all referenced JSON Schemas before touching data shapes.
3. `CONTRIBUTING.md` and the current ADRs in `docs/decisions/`.
4. `docs/verification.md` and `docs/traceability.json` before claiming completion.

## Development order

1. Confirm the requirement, acceptance signal, and module owner.
2. Change a contract only with producer and consumer review; update Schema, examples, semantic checks, and consumers together.
3. Implement behind the existing service or adapter boundary, then connect the UI.
4. Add the smallest relevant test, run it, and record actual evidence.
5. Update traceability only to the status the evidence supports.

## Module boundaries

- A owns scene geometry, exits, adjustable parameters, and geometry validation.
- B owns 3D / top-down rendering, particles, heatmaps, and playback.
- C owns PDE/FNO execution, numerical checks, and experiment records.
- D owns metrics, hotspots, candidate generation, ranking, and fair comparison.
- E owns the service shell, persistence, task orchestration, API, integration, deployment, and acceptance coordination.

Do not implement another member's core capability inside E just to make a demo look complete. Use explicit Mock or placeholder data, label its source, and keep replaceable boundaries.

## Validation

```powershell
python tools/check_repository.py
python -m unittest discover -s tests/contracts -v
python -m unittest discover -s tests/system -v
```

For the W1 system shell, also start `python -m apps.api.crowdturb` and verify the relevant user-visible flow. A file existing, a Schema passing, or a screenshot alone is not product acceptance.

## AI assistance

- AI-generated changes are drafts. The person submitting them remains responsible for correctness, scope, licensing, and review.
- Record material AI assistance in `docs/ai/usage-log.md` with task, inputs, output, verification, and review status.
- Never use AI output as experimental evidence. PDE/FNO accuracy, performance, and optimization claims require real runs and reproducible records.
- Never send secrets, tokens, private keys, or unnecessary personal data to an AI tool or commit them.
- Do not change contract semantics, mark a requirement verified, or fabricate a result just because an AI agent suggested it.

## Git and evidence

- Use `Issue → short branch → focused PR → review → checks → merge` when the user or team requests Git actions.
- Do not commit unless asked. Do not rewrite history or discard unrelated work.
- Keep Mock, PDE, and FNO provenance distinct in code, UI, tests, and reports.
- Prefer Chinese product documentation and English identifiers, matching the existing repository style.

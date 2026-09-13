# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## AccuFlow visual target

- Source of truth: `reference-option-1.png`, selected by the user on 2026-09-12.
- Preserve the calm light operations-console direction: slim left navigation, a tracked-stock table as the primary surface, and recent reports as the secondary surface.
- Use one unified score and never introduce A/B modes, product tiers, brokerage positions, P&L, or order-entry controls.
- Keep IBKR connection and data-freshness status visible without turning the product into a dense trading terminal.
- Support both light and dark interface themes, defaulting to the system preference and remembering the user's explicit choice.

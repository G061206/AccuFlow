# AccuFlow Web Console — Design QA

- Source visual truth: `E:\AccuFlow\web-console\reference-option-1.png`
- Implementation URL: `http://localhost:4173`
- Comparison viewport: 1440 × 1024 CSS px, device scale factor 1
- Source pixels: 1487 × 1058
- Implementation pixels: 1440 × 1024
- Density normalization: the source is rendered into the same 1.40625 aspect-ratio frame as the implementation; its original aspect ratio is 1.40548, so distortion is below 0.06%
- State: default “跟踪股票” page with five stock rows and three recent reports

## Full-view comparison evidence

- Combined normalized comparison: `E:\AccuFlow\web-console\qa-artifacts\comparison-main.png`
- Direct implementation capture: `E:\AccuFlow\web-console\qa-artifacts\implementation-main.png`
- Report drawer capture: `E:\AccuFlow\web-console\qa-artifacts\implementation-report.png`
- Narrow responsive capture: `E:\AccuFlow\web-console\qa-artifacts\implementation-narrow.png`

The combined image was inspected at its original 1920 × 724 pixels. Sidebar width, content origin, page heading, add-stock form, stock table, report section, borders, colors, and vertical rhythm align with the selected source.

## Focused region comparison evidence

A separate crop was not needed: the original-resolution implementation screenshot and the 1920-pixel-wide combined comparison keep the navigation, add form, all table headers, stock status pills, action buttons, and recent-report rows legible in one view. The report drawer was additionally inspected in its dedicated 1440 × 1024 capture.

## Findings

- No open P0, P1, or P2 issues.
- Minor non-blocking differences: Phosphor's three-dot row action icon replaces the source's menu glyph, and font rasterization varies slightly from the generated reference. Both preserve the intended hierarchy and affordance.

## Comparison history

- Pass 1: browser capture was initially blocked because the desktop browser surface rejected local URLs. Resolved by installing the approved Playwright browser runtime.
- Pass 2: found P2 fidelity differences in AMD's state presentation, homepage description, and add button adornment. Fixed by restoring the amber “数据不完整” status, matching the selected copy, and removing the extra plus icon.
- Pass 3: repeated normalized full-view comparison; no actionable P0, P1, or P2 issue remained.

## Primary interactions tested

- Add a valid ticker and reject a duplicate ticker.
- Pause and resume tracking.
- Open the overflow menu and remove a ticker.
- Open and close a historical report drawer.
- Navigate to report history, search by ticker, and filter by report type.
- Open and close the user menu.
- Change a settings toggle.
- Verify the 820-pixel narrow layout keeps primary controls usable.

## Runtime checks

- Browser console and page errors: none during desktop or narrow tests.
- Playwright interaction and responsive tests: 2 passed.
- Production Vite build: passed.
- Sites packaging tests: 4 passed.

final result: passed

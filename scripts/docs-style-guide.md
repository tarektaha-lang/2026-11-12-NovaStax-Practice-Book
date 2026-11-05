# Dark Documentation Page — Style Guide

A specification for a single-file, dark-themed HTML documentation page. Give this
file to an LLM and ask it to "produce documentation in this style"; it contains
everything needed to reproduce the look without seeing a previous project.

## Overview

- **One self-contained `.html` file**: all CSS inline in a `<style>` block in
  `<head>`. No external stylesheets, fonts, frameworks, or JavaScript.
- **Look and feel**: dark GitHub-adjacent palette, sans-serif prose, monospace
  accents, sticky sidebar navigation, generous vertical rhythm. Calm and
  technical; no decoration for its own sake, no emojis.
- **Structure**: a fixed left sidebar (table of contents) and a flexible right
  content column, centered in a 1240px container.

## Color palette

Define these CSS custom properties on `:root` and use them exclusively —
no ad-hoc colors except the few listed as literals:

```css
:root {
  --bg: #0f1115;        /* page background */
  --panel: #171a21;     /* card/panel background */
  --panel-2: #1c2029;   /* pills, slightly lighter panel */
  --border: #2a2f3a;    /* all borders and rules */
  --text: #e8eaed;      /* headings, primary text */
  --muted: #9aa0a6;     /* secondary text, labels, captions */
  --accent: #6ea8fe;    /* blue: links, primary highlights, stat values */
  --accent-2: #7ee787;  /* green: success, h4 headings, second data series */
  --warn: #ffb86b;      /* orange: warnings */
  --code-bg: #0b0d11;   /* code blocks, darkest surface */
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
```

Body copy inside `p` and `li` uses the literal `#d4d7dc` (slightly dimmer than
`--text`); `h3` uses `#fff`.

## Typography

- **Prose**: system sans stack
  `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`,
  `line-height: 1.65`.
- **Code, pills, stat values, table cells with identifiers**: the `--mono` stack.
- **Scale**: h1 `2rem` (line-height 1.1) · section h2 `1.45rem` · h3 `1.12rem` ·
  h4 `0.98rem`, colored `--accent-2` · body `1rem` · captions/labels
  `0.74–0.8rem`.
- **Small-caps labels**: nav group headings, table headers, and stat labels are
  uppercase, `0.74–0.8rem`, letter-spacing `0.04–0.08em`, colored `--muted`.

## Layout grid

- Container: `display: flex; max-width: 1240px; margin: 0 auto; gap: 32px;
  padding: 0 24px;` (1240 = 12-column grid heritage: 12 × 80px + 24px gutters).
- **Sidebar**: fixed `width: 280px; flex-shrink: 0;` sticky
  (`position: sticky; top: 0`), `max-height: 100vh; overflow-y: auto;`,
  `border-right: 1px solid var(--border)`. Height sizes to content — the
  divider line ends after the last link, not at the viewport bottom.
- **Content column**: `flex: 1; min-width: 0; padding: 32px 0;`.
- **Responsive**: below `820px`, hide the nav entirely and reduce container
  padding to 16px.

### Vertical rhythm and alignment (deliberate details)

These were tuned by hand; keep them:

1. Sidebar and content both begin 32px from the top.
2. The first nav group heading gets an extra `margin-top` so its **text
   baseline aligns with the h1's baseline** (with the scale above the value is
   `15.5px`; if sizes change, re-derive it).
3. The sidebar's bottom padding mirrors the top: `32px + the baseline nudge`
   (i.e. `47.5px`), so the space under the last nav link equals the space
   above the first group heading.
4. The content column ends with the same 32px breathing room as the top
   (no oversized footer padding).
5. Sections are separated by `48px` top margins; major thematic breaks use a
   plain `<hr>` (1px `--border`, 40px margins).

## Page skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{Project Name} — Documentation</title>
  <style>/* full CSS here */</style>
</head>
<body>
<div class="layout">
  <nav>
    <h2>{Group}</h2>
    <a href="#{id}">{Section title}</a>
    ...
  </nav>
  <main>
    <header class="hero">
      <h1>{Project Name}</h1>
      <div class="sub">{One- or two-sentence summary of the project.}</div>
      <div>
        <span class="pill">{key fact}</span>
        <span class="pill">{key fact}</span>
      </div>
    </header>

    <h2 class="section" id="{id}">{Section title}</h2>
    <p>...</p>

    <hr />  <!-- between major thematic groups -->

    <p class="toc-foot">{Closing line: what this file documents and a
    keep-in-sync reminder.}</p>
  </main>
</div>
</body>
</html>
```

- The **nav** mirrors the sections exactly, grouped under 2–5 small-caps
  headings (e.g. Overview / Using it / Reference). Every `h2.section` has an
  `id` and a nav link.
- The **hero** is separated from the body by a 1px bottom border. Pills are
  mono-font rounded chips carrying the headline facts (counts, sizes, formats,
  status) — 3 to 5 of them.

## Components

Use these building blocks; don't invent new ones unless the content demands it.

### Panels (callouts)

```html
<div class="panel note">…</div>   <!-- blue left edge: tips, context -->
<div class="panel warn">…</div>   <!-- orange left edge: caveats, gotchas -->
<div class="panel good">…</div>   <!-- green left edge: confirmations, successes -->
```

Rounded 12px cards on `--panel` background with a 3px colored left border.
Start the text with a `<strong>` lead-in phrase ("**Fully offline.** …").

### Tables

Full-width, no vertical rules, 1px `--border` row separators, uppercase muted
`th`. Add `class="mono"` to cells holding file names, commands, or URLs, and
`class="num"` (right-aligned, tabular numerals) for numeric columns. Use tables
for scripts/files, options, routes, requirements — any set of name+description
pairs.

### Stat cards

A `.stat-grid` (auto-fit, min 160px) of `.stat` cards: big mono `--accent`
value over a small uppercase muted label. Use once, near the top, for the 3–5
headline numbers.

### Horizontal bar charts

Pure CSS, no libraries: rows of
`label | <div class="bar" style="width:{pct}%"> | value`.
Blue gradient bars by default; add class `alt` for a green second series.
Scale the widths so the largest bar spans ~70% and keep the printed value
honest (width may be proportionally exaggerated for readability, the number is
the truth). Follow every chart with a `.caption` line stating the data source
or sample size.

### Code

Inline `code` for identifiers, paths, commands. `pre` blocks (darkest surface,
10px radius) for file trees, JSON examples, terminal commands, and code
snippets. Keep examples real and trimmed with `…` / `...` where needed.

### Links

`<a class="inline" href="…">` — accent blue, underline on hover only.

## Content conventions

- Sidebar groups read like a story: Overview → the work → the details →
  Reference.
- Sections open with a short prose paragraph, then reach for a component
  (table, panel, chart, code) instead of long paragraphs.
- Bold lead-ins for list items ("**Process custody.** A download launched…").
- State numbers precisely (35,908 files, 207.7 GiB) and cite where they came
  from in captions or a Sources section.
- End with `p.toc-foot`: a one-line muted footer saying what the file
  documents and to keep it in sync.
- Tone: factual, first-person-plural or impersonal, no marketing language,
  no emojis, sentence-case headings.

## Full CSS reference

Copy this verbatim as the starting point (it encodes everything above):

```css
:root {
  --bg: #0f1115; --panel: #171a21; --panel-2: #1c2029; --border: #2a2f3a;
  --text: #e8eaed; --muted: #9aa0a6; --accent: #6ea8fe; --accent-2: #7ee787;
  --warn: #ffb86b; --code-bg: #0b0d11;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
       Roboto, sans-serif; background: var(--bg); color: var(--text);
       line-height: 1.65; }
.layout { display: flex; max-width: 1240px; margin: 0 auto; gap: 32px;
          padding: 0 24px; }
nav { position: sticky; top: 0; align-self: flex-start; max-height: 100vh;
      overflow-y: auto; width: 280px; flex-shrink: 0;
      padding: 32px 0 47.5px; border-right: 1px solid var(--border); }
nav h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em;
         color: var(--muted); margin: 24px 0 8px; }
nav h2:first-child { margin-top: 15.5px; } /* baseline-aligns with the h1 */
nav a { display: block; color: var(--muted); text-decoration: none;
        font-size: 0.88rem; padding: 3px 0; }
nav a:hover { color: var(--accent); }
main { flex: 1; min-width: 0; padding: 32px 0; }
header.hero { padding: 0 0 24px; border-bottom: 1px solid var(--border);
              margin-bottom: 32px; }
header.hero h1 { font-size: 2rem; line-height: 1.1; margin: 0 0 6px; }
header.hero .sub { color: var(--muted); font-size: 1.02rem; }
.pill { display: inline-block; background: var(--panel-2);
        border: 1px solid var(--border); border-radius: 999px;
        padding: 2px 12px; font-size: 0.78rem; color: var(--muted);
        margin: 8px 6px 0 0; font-family: var(--mono); }
h2.section { font-size: 1.45rem; margin: 48px 0 12px; padding-top: 12px; }
h3 { font-size: 1.12rem; margin: 28px 0 8px; color: #fff; }
h4 { font-size: 0.98rem; margin: 18px 0 6px; color: var(--accent-2); }
p, li { color: #d4d7dc; }
code { font-family: var(--mono); font-size: 0.86em; background: var(--code-bg);
       border: 1px solid var(--border); border-radius: 5px; padding: 1px 5px;
       color: #e6edf3; }
pre { background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 16px 18px; overflow-x: auto;
      font-family: var(--mono); font-size: 0.84rem; line-height: 1.5; }
pre code { background: none; border: none; padding: 0; }
.panel { background: var(--panel); border: 1px solid var(--border);
         border-radius: 12px; padding: 18px 22px; margin: 16px 0; }
.note { border-left: 3px solid var(--accent); }
.warn { border-left: 3px solid var(--warn); }
.good { border-left: 3px solid var(--accent-2); }
table { width: 100%; border-collapse: collapse; margin: 14px 0;
        font-size: 0.9rem; }
th, td { text-align: left; padding: 9px 12px;
         border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 0.8rem;
     text-transform: uppercase; letter-spacing: 0.04em; }
td.mono, td .mono { font-family: var(--mono); font-size: 0.84rem; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.stat-grid { display: grid;
             grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
             gap: 12px; margin: 20px 0; }
.stat { background: var(--panel); border: 1px solid var(--border);
        border-radius: 12px; padding: 16px; text-align: center; }
.stat .value { display: block; font-family: var(--mono); font-size: 1.45rem;
               font-weight: 700; color: var(--accent); }
.stat .label { font-size: 0.74rem; text-transform: uppercase;
               letter-spacing: 0.06em; color: var(--muted); }
.bar-row { display: grid; grid-template-columns: 6rem 1fr 4rem;
           align-items: center; gap: 10px; font-size: 0.84rem; margin: 5px 0; }
.bar-row .bar { background: linear-gradient(90deg, var(--accent), #4a7dd6);
                height: 14px; border-radius: 3px; min-width: 2px; }
.bar-row.alt .bar { background: linear-gradient(90deg, var(--accent-2), #4fa85f); }
.bar-row .pct { color: var(--muted); font-variant-numeric: tabular-nums;
                text-align: right; font-family: var(--mono); font-size: 0.8rem; }
.caption { color: var(--muted); font-size: 0.78rem; margin: 6px 0 18px; }
ol, ul { padding-left: 22px; }
li { margin: 4px 0; }
hr { border: none; border-top: 1px solid var(--border); margin: 40px 0; }
.toc-foot { color: var(--muted); font-size: 0.78rem; margin-top: 32px;
            margin-bottom: 0; }
a.inline { color: var(--accent); text-decoration: none; }
a.inline:hover { text-decoration: underline; }
@media (max-width: 820px) {
  nav { display: none; }
  .layout { padding: 0 16px; }
}
```

## Prompt snippet

To commission a page in this style, attach this file and say something like:

> Write documentation for {project} as a single self-contained HTML file in
> the style described in docs-style-guide.md. Follow its palette, layout,
> components, and content conventions exactly. Sections: {list}. Headline
> stats for the stat cards: {facts}.

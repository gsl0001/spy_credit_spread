---
title: Vault README
tags: [meta]
---

# Obsidian Vault — SPY Options Trading Engine

This folder is an **Obsidian vault**. To browse it the way it was designed:

1. Install [Obsidian](https://obsidian.md) (free)
2. **Open folder as vault** → select `docs/obsidian/`
3. Open `00 - Start Here.md`

You'll get:

- Mermaid diagrams rendered visually
- Wikilinks (`[[Like This]]`) you can click
- Callouts (the colored boxes) styled
- Graph view showing how every page connects
- Tag explorer for `#strategy`, `#topology`, etc.

> [!tip] Don't have Obsidian?
> The pages still render in any Markdown viewer (GitHub, VS Code, etc.) — you'll just see the wikilinks as `[[plain text]]` and the callouts as block quotes. Mermaid diagrams render in GitHub.

## Folder map

```
docs/obsidian/
├── 00 - Start Here.md       ← begin here
├── 01 - Overview/
│   ├── What Is This.md
│   ├── How It Works.md
│   └── System Architecture.md
├── 02 - Getting Started/
│   ├── Installation.md
│   ├── First Backtest.md
│   └── Connecting Brokers.md
├── 03 - The Six Modes/
│   ├── Live Mode.md
│   ├── Paper Mode.md
│   ├── Backtest Mode.md
│   ├── Scanner Mode.md
│   ├── Journal Mode.md
│   └── Risk Mode.md
├── 04 - Strategies/
│   ├── Strategy Overview.md
│   ├── Consecutive Days.md
│   ├── Combo Spread.md
│   └── Building Your Own.md
├── 05 - Option Topologies/
│   ├── Topology Overview.md
│   ├── Vertical Spread.md
│   ├── Long Call and Put.md
│   ├── Straddle.md
│   ├── Iron Condor.md
│   └── Butterfly.md
├── 06 - Filters and Risk/
│   ├── Entry Filters.md
│   └── Exit Controls.md
├── 07 - Performance Analytics/
│   └── Metrics Explained.md
├── 08 - API Reference/
│   └── REST Endpoints.md
└── Glossary.md
```

## Conventions

- **Wikilinks** — every cross-reference uses `[[Page Title]]` syntax
- **Frontmatter** — every page has `title` and `tags`
- **Callouts** — `> [!info]`, `> [!warning]`, `> [!tip]`, `> [!example]`, `> [!success]`, `> [!danger]`
- **Mermaid** — every page that explains flow includes a diagram
- **No emojis** — Obsidian's callout icons handle the visual cues

## Editing

- Edit any `.md` file in any text editor or in Obsidian itself
- Pull requests for fixes are welcome
- Diagrams use [Mermaid](https://mermaid.js.org) — rendered live in Obsidian

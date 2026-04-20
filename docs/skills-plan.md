# Plan: Skills System Implementation

## Overview
Add markdown-based skills to the Discord bot following the Agent Skills spec (Claude Code / Cursor / Gemini CLI compatible). Skills are loaded on-demand and injected into context only when triggered, keeping the system prompt lean.

## Architecture

### Skill Structure
```
skills/
├── SKILLS.md           # Index file listing all available skills (name + description only)
├── web-search/
│   ├── SKILL.md        # Skill definition
│   └── references/     # Detailed docs (optional)
├── discord-tools/
│   ├── SKILL.md
└── general/
    └── SKILL.md
```

### Each SKILL.md Format (Agent Skills spec)
```yaml
---
name: web-search
description: >
  Use when the user asks to search the web, look up current information,
  find news, or check real-time data. Keywords: search, find, look up, web, URL.
version: 1.0.0
---

## Instructions
1. Use the tavily_search MCP tool to perform the search
2. Summarize results concisely (3-5 bullet points)
3. Cite sources with URLs

## Output format
- Brief summary
- Links to sources
- No speculation beyond source material
```

### Skill Loader
File: `skills/__init__.py`

On bot startup:
1. Scan `skills/` directory for subdirectories
2. Parse each `SKILL.md` — extract YAML frontmatter (`name`, `description`, `version`, `metadata`) + markdown body
3. Store index: `{name: description}` in a registry for trigger matching
4. Store full instructions in a skill database keyed by name
5. Write `skills/SKILLS.md` index file (for human readability)

### Trigger System
Two trigger types:
- **Auto-trigger**: Match user message against skill `description` keywords (fuzzy/partial match, threshold-based)
- **Manual trigger**: `/skill-name` slash command that always loads that skill

### Context Injection (Progressive Disclosure)
When a user sends a message:
1. Build base prompt (system message + history)
2. Find matching skills (auto-trigger or manual)
3. Inject each matching skill's instructions into the prompt
4. Map referenced tools (e.g., "tavily_search") to registered MCP tools
5. Pass to Ollama

When no skill matches in a subsequent message:
- If the conversation was about a different topic, remove previous skill instructions
- If the conversation is continuous on the same topic, keep them

### Tool Mapping
Skills reference tools (MCP tools, internal functions). The loader resolves references:
- Skill says "tavily_search" → resolves to MCP tool `tavily_search`
- Skill says "get_time" → resolves to internal function `get_current_time()`
- Resolved tools added to `mcp_tools` list before calling Ollama

## Implementation Steps

### Step 1: Skill Parser (`skills/parser.py`)
- Parse `SKILL.md` files: YAML frontmatter extraction
- Validate required fields (`name`, `description`)
- Extract optional fields (`version`, `metadata`)
- Return structured skill object

### Step 2: Skill Registry (`skills/registry.py`)
- Load all skills from `skills/` directory
- Build name→skill mapping
- Build description keyword index for auto-trigger
- Write `SKILLS.md` index file
- Register on startup, re-scan on config reload

### Step 3: Trigger Engine (`skills/triggers.py`)
- `match_skill(message_text) → list[skill]` — fuzzy match against descriptions
- `match_slash_command(command) → skill | None` — exact match for `/skill-name`
- Confidence scoring for auto-matching (e.g., 0.0-1.0 threshold)

### Step 4: Context Builder Integration (`run.py`)
Modify `build_prompt()` to:
1. Find matching skills for the message
2. Inject skill instructions into system prompt
3. Resolve tool references from matched skills
4. Handle skill switching mid-conversation

### Step 5: Example Skills
Create 2-3 starter skills:
- **web-search**: Uses Tavily MCP tool, auto-triggers on "search", "find", etc.
- **discord-info**: Uses Discord tools, info about guild/channels/members
- **general**: Default skill with basic instructions

### Step 6: SKILLS.md Generator
Auto-generate `skills/SKILLS.md` (human-readable index of available skills with descriptions). Updated whenever skills change.

## Files to Create
1. `skills/__init__.py` — loader, registry, init
2. `skills/parser.py` — SKILL.md file parsing
3. `skills/registry.py` — skill registry
4. `skills/triggers.py` — auto and manual trigger matching
5. `skills/SKILLS.md` — index of all available skills
6. `skills/web-search/SKILL.md` — web search skill example
7. `skills/discord-toolkit/SKILL.md` — Discord info skill example
8. `skills/general/SKILL.md` — default general skill

## Files to Modify
1. `run.py` — import skills on startup, integrate into `build_prompt()` and `evaluate_should_respond()`
2. `config.yaml` — add skills section (optional: disable/enable, threshold settings)

## Config Changes
```yaml
skills:
  enabled: true
  trigger_threshold: 0.3   # Auto-trigger match confidence (0.0-1.0)
  auto_invoke: true        # Auto-load skills based on trigger matching
  manual_only: false       # If true, only use /skill-name commands
```

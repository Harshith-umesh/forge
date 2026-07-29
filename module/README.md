# FORGE AI Context Module

This module contains reusable AI agent skills for working with the FORGE codebase.
Skills follow the [AgentSkills.io](https://agentskills.io/specification) standard and
can be installed into any supported coding agent using [Lola](https://github.com/LobsterTrap/lola).

## Available Skills

| Skill | Description |
|-------|-------------|
| `forge-review` | Pre-commit review that checks local changes against FORGE rules in `AGENTS.md` |

## Installation

### Prerequisites

Install Lola:

```bash
# Using uv (recommended)
uv tool install lola-ai

# Or using pip
pip install lola-ai
```

### Register the module

From the forge repository root:

```bash
lola mod add .
```

### Install into your project

**Important:** Run these commands from the forge project directory so skills are
installed at the project level, not globally.

#### Cursor

```bash
cd /path/to/forge
lola install forge -a cursor
```

This creates:
```
forge/
  .cursor/
    skills/
      forge-review/
        SKILL.md
        scripts/
          gather-diff.sh
```

The skill will appear in Cursor's skill list and can be triggered by asking
the agent to "review my changes" or "forge review" before committing.

#### Claude Code

```bash
cd /path/to/forge
lola install forge -a claude-code
```

This creates:
```
forge/
  .claude/
    skills/
      forge-review/
        SKILL.md
        scripts/
          gather-diff.sh
```

The skill is available in Claude Code when working inside the forge project.

#### All detected agents at once

```bash
cd /path/to/forge
lola install forge
```

Lola auto-detects which agents are available and installs to all of them.

### Declarative installation (team workflow)

Add a `.lola-req` file to the forge project root:

```
# .lola-req — FORGE AI context module
.
```

Then any team member clones the repo and runs:

```bash
lola sync
```

This installs the module into whichever agents they have configured.

## Manual installation (without Lola)

If you prefer not to use Lola, copy the skill directory manually.

#### Cursor

```bash
mkdir -p .cursor/skills/forge-review
cp -r module/skills/forge-review/* .cursor/skills/forge-review/
```

#### Claude Code

```bash
mkdir -p .claude/skills/forge-review
cp -r module/skills/forge-review/* .claude/skills/forge-review/
```

## Updating

After pulling new changes that update the module:

```bash
lola mod update forge
lola install forge
```

Or with declarative mode:

```bash
lola sync
```

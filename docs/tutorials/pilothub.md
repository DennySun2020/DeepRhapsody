# PilotHub — Community Skill Registry

PilotHub is a community-driven registry for sharing debugging skills that extend NeuralDebug's capabilities.

## Using Skills

### Search for Skills

```bash
NeuralDebug hub search "memory debugging"
NeuralDebug hub search "performance profiling"
```

### Install a Skill

```bash
NeuralDebug hub install memory-debugger
NeuralDebug hub install memory-debugger --version 1.0.0
```

Skills are installed to `~/.NeuralDebug/skills/` by default.

### List Installed Skills

```bash
NeuralDebug hub list
```

### Update Skills

```bash
# Update all
NeuralDebug hub update

# Update a specific skill
NeuralDebug hub update memory-debugger
```

### Uninstall a Skill

```bash
NeuralDebug hub uninstall memory-debugger
```

## Creating Skills

A skill is a directory containing a `SKILL.md` file with YAML frontmatter and a prompt body.

### SKILL.md Format

```markdown
---
name: memory-debugger
description: Debug memory leaks using Valgrind and AddressSanitizer
version: 1.0.0
author: DennySun2020
tags: [memory, c, cpp, debugging]
requires:
  bins: [valgrind]
---

# Memory Debugger

When debugging memory issues, use the following approach:

1. First, run Valgrind to detect leaks:
   ```bash
   valgrind --leak-check=full ./program
   ```

2. For more detailed analysis, compile with AddressSanitizer:
   ```bash
   gcc -fsanitize=address -g -o program program.c
   ```

3. Analyze the output to identify the source of memory errors.
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique skill identifier (kebab-case) |
| `description` | Yes | Brief description of what the skill does |
| `version` | Yes | Semantic version (e.g., `1.0.0`) |
| `author` | No | Your username |
| `tags` | No | Searchable tags |
| `requires.bins` | No | Required binaries (e.g., `[valgrind]`) |
| `requires.platforms` | No | Supported platforms (e.g., `[linux, darwin]`) |
| `homepage` | No | URL to skill repository or docs |

### Publish a Skill

```bash
NeuralDebug hub publish ./my-skill
```

## How Skills Work

When the standalone agent starts, it:
1. Scans `~/.NeuralDebug/skills/` for installed skills
2. Loads each skill's prompt content
3. Includes skill prompts in the agent's system context
4. Exposes each skill as a callable tool (prefixed with `skill_`)

Skills are prompt-based — they provide specialized knowledge and instructions to the agent without executing arbitrary code.

## Configuration

Override the default skills directory and hub URL:

```bash
# Environment variables
export NeuralDebug_SKILLS_DIR=~/my-skills
export PILOTHUB_URL=https://my-registry.example.com/api/v1

# Or in config file (~/.NeuralDebug/config.yaml)
skills_dir: ~/my-skills
hub_url: https://my-registry.example.com/api/v1
```

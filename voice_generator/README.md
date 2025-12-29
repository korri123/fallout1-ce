# Voice Generator

Generates ElevenLabs voice design prompts for Fallout 1 NPCs using the Claude Agent SDK.

## Setup

```bash
cd voice_generator
python3 -m venv venv
source venv/bin/activate
pip install claude-code-sdk
```

## Usage

### Single NPC from npc_dialogue.json

```bash
python3 voice_designer.py --npc <json_file> <npc_key>
```

Example:
```bash
python3 voice_designer.py --npc ../tools/npc_dialogue.json killian
```

### All NPCs (batch mode)

```bash
python3 voice_designer.py --npc ../tools/npc_dialogue.json --all
```

### Simple character lookup

```bash
python3 voice_designer.py "Character Name" "Optional description"
```

Example:
```bash
python3 voice_designer.py "Killian Darkwater" "Owner of Darkwaters General Store in Junktown"
```

### Custom JSON input

```bash
python3 voice_designer.py --json <json_file>
```

JSON format:
```json
{
  "name": "Character Name",
  "description": "Character description",
  "dialogue_samples": ["line 1", "line 2"]
}
```

## Options

| Flag | Description |
|------|-------------|
| `--force` | Regenerate even if cached |
| `--npc` | Load from npc_dialogue.json format |
| `--json` | Load from simple JSON format |
| `--all` | Process all NPCs (with --npc) |

## Caching

Results are cached in `voice_cache.json` to avoid redundant API calls. The cache key is the character name (lowercased, spaces replaced with underscores).

- First run: `[generating]` - queries Claude, saves to cache
- Subsequent runs: `[cached]` - returns cached result instantly
- Use `--force` to bypass cache and regenerate

## NPC Dialogue Format

The `--npc` option expects JSON in the format produced by `tools/extract_npc_dialogue.py`:

```json
{
  "dialogue": {
    "npc_key": {
      "script_name": "npc_key",
      "npc_name": "Display Name",
      "voice_info": {
        "gender": "Male",
        "creature_type": "Human",
        "appearance": "You see a grizzled merchant.",
        "speaking_style": "Gruff",
        "sample_lines": ["Line 1", "Line 2"]
      },
      "npc_lines": [
        {"id": 100, "text": "Dialogue line"}
      ]
    }
  }
}
```

## Output

The tool generates concise voice design prompts suitable for ElevenLabs text-to-speech:

```
A confident, hearty male voice with warm, gregarious delivery. Mid-to-late 30s,
athletic build reflected in his tone. Speaks with natural confidence and good
humor, punctuated by genuine laughter.
```

## How It Works

1. Loads character info (name, gender, appearance, dialogue samples)
2. Checks cache for existing result
3. If not cached, queries Claude (Haiku) with character context
4. Claude may search Fallout wiki for additional character info
5. Extracts clean voice prompt from response (strips preamble/sources)
6. Caches and returns the result

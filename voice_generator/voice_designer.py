"""
Voice Designer for Fallout 1 Characters

Uses Claude Agent SDK with Haiku to generate ElevenLabs voice design prompts
based on character descriptions and optional web research.
"""

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock


# ElevenLabs API character limit for voice design prompts
ELEVENLABS_CHAR_LIMIT = 1000


def truncate_to_limit(text: str, limit: int = ELEVENLABS_CHAR_LIMIT) -> str:
    """
    Truncate text to character limit, breaking at sentence boundary if possible.
    """
    if len(text) <= limit:
        return text

    # Try to break at a sentence boundary
    truncated = text[:limit]
    last_period = truncated.rfind('. ')
    if last_period > limit * 0.7:  # Only use if we keep at least 70%
        return truncated[:last_period + 1]

    # Fall back to word boundary
    last_space = truncated.rfind(' ')
    if last_space > limit * 0.8:
        return truncated[:last_space]

    return truncated


def extract_voice_prompt(text: str) -> str:
    """
    Extract just the voice design prompt from LLM output.
    Looks for content between --- delimiters.
    """
    # Look for content between --- delimiters
    match = re.search(r"^---\s*\n(.*?)\n---", text, flags=re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback: try to find any --- block (might not have leading newline)
    match = re.search(r"---\s*(.*?)\s*---", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()

    # Last resort: return cleaned text if no delimiters found
    return text.strip()

# Default cache file location
DEFAULT_CACHE_FILE = Path(__file__).parent / "voice_cache.json"


@dataclass
class CharacterInfo:
    """Information about a Fallout 1 character."""
    name: str
    description: str = ""
    dialogue_samples: list[str] | None = None
    gender: str = ""
    creature_type: str = ""
    appearance: str = ""
    faction: str = ""

    @classmethod
    def from_npc_entry(cls, npc_key: str, entry: dict) -> "CharacterInfo":
        """
        Create CharacterInfo from an NPC dialogue JSON entry.

        Args:
            npc_key: The key/id of the NPC in the dialogue dict
            entry: The NPC entry dict from npc_dialogue.json

        Returns:
            CharacterInfo populated with all available data
        """
        voice_info = entry.get("voice_info", {})

        # Get all dialogue lines from npc_lines
        npc_lines = entry.get("npc_lines", [])
        dialogue_samples = [line["text"] for line in npc_lines]

        # Use npc_name if available, otherwise use the script_name/key
        name = entry.get("npc_name") or entry.get("script_name") or npc_key
        # Capitalize the name if it's lowercase
        if name and name.islower():
            name = name.title()

        return cls(
            name=name,
            description=entry.get("description", ""),
            dialogue_samples=dialogue_samples,
            gender=voice_info.get("gender", ""),
            creature_type=voice_info.get("creature_type", ""),
            appearance=voice_info.get("appearance", ""),
            faction=voice_info.get("faction", ""),
        )

    def cache_key(self) -> str:
        """Generate a stable cache key for this character."""
        # Use name as the primary key, lowercased and normalized
        return self.name.lower().replace(" ", "_")

    def to_prompt(self) -> str:
        """Format character info for the prompt."""
        parts = [f"Character Name: {self.name}"]

        if self.gender:
            parts.append(f"Gender: {self.gender}")

        if self.creature_type:
            parts.append(f"Creature Type: {self.creature_type}")

        if self.appearance:
            parts.append(f"Appearance: {self.appearance}")

        if self.faction:
            parts.append(f"Faction: {self.faction}")

        if self.description:
            parts.append(f"Description: {self.description}")

        if self.dialogue_samples:
            parts.append("Sample Dialogue:")
            for line in self.dialogue_samples:
                parts.append(f'  - "{line}"')

        return "\n".join(parts)


class VoiceCache:
    """Cache for generated voice prompts."""

    def __init__(self, cache_file: Path = DEFAULT_CACHE_FILE):
        self.cache_file = cache_file
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self):
        """Load cache from disk."""
        if self.cache_file.exists():
            with open(self.cache_file, 'r') as f:
                self._cache = json.load(f)

    def _save(self):
        """Save cache to disk."""
        with open(self.cache_file, 'w') as f:
            json.dump(self._cache, f, indent=2)

    def get(self, key: str) -> str | None:
        """Get cached voice prompt by key."""
        return self._cache.get(key)

    def set(self, key: str, value: str):
        """Set and persist a voice prompt."""
        self._cache[key] = value
        self._save()

    def __contains__(self, key: str) -> bool:
        return key in self._cache


SYSTEM_PROMPT = """You generate ElevenLabs voice design prompts for Fallout 1 characters.

**Output:** 150-300 characters ideal (hard limit: 1000). Voice qualities ONLY, no personality or backstory.

## Style Rules
1. **Simple words a kid would understand.** No fancy thesaurus garbage.
2. **Be colorful and fun.** Boring = bad. Give each voice CHARACTER.
3. **Say what you mean:**
   - "Rural American accent" → just say "redneck"
   - "Exhibits signs of mental instability" → "sounds crazy"
   - "Elderly gentleman" → "old man"
   - "Youthful vocal quality" → "young-sounding"
4. **No redundancy.** Say it once, say it right, move on. Don't repeat yourself with synonyms.
5. **Keep it SHORT.** Every word must earn its place.
6. **Pitch**: Imagine what kinda pitch this type of person would have in a cartoon, and state it in the prompt.

## Technical Rules
1. **Pacing:** Never say "slow" or "deliberate" alone—makes it sluggish. Say "unhurried but natural" instead.
2. **Performance:** Include acting direction. Without this you get boring robot narration.
3. **No Fallout jargon:** Ghoul → "throat sounds like gargling gravel from radiation"; Super Mutant → "big dumb monster, sounds like an orc"; Peasant → "poor worker."
4. **"Children" = Children of the Cathedral cult (adults).** Only use kid voice if ACTUALLY a child. If Creature Type says Child, make it a TEENAGER. NEVER INCLUDE "BOY", "KID" OR THE AGE, it will be rejected.
5. **Super Mutants/Nightkin:** Monster voices! Deep growly beast sounds, not just "deep voice." Think orc or ogre.
6. **American accent:** unless there's a good reason otherwise.
7. **Vary male voices:** Not everyone is a medium pitched guy. Nerds sound nerdy, young guys sound young.
8. **Specify gender:** The prompt needs to always include the sex of the character, directly or indirectly.
9. **Exaggerate, exaggerate, exaggerate:** For the prompt to be effective you need to exaggerate every detail, almost to a comical extent.
10. Remove any jargon that might confuse the model. Anything unnecessary must be removed.
11. Create fun interesting descriptions instead of bland noes, like if this were a character on The Simpsons, what would he sound like? 

## Output Format
---
[Voice description here]
---

## Example
---
Gruff old man, 50s. Redneck drawl. Sounds like he's been chewing dirt. Bossy but not a jerk about it. Has a deep voice.
---

## AVOID - This is a boring prompt:
---
Male guard, medium-low pitch. Sounds like a security guard at an office building - polite but firm, a bit bored from the quiet shift but still alert. Direct, no-nonsense talker. American accent.
---

## BETTER:
---
Male, mid-40s. Deep gravelly voice. Sounds perpetually tired but trying to seem important. Midwestern American accent.
---
"""


async def generate_voice_prompt(
    character: CharacterInfo,
    cache: VoiceCache | None = None,
    force: bool = False,
) -> str:
    """
    Generate an ElevenLabs voice design prompt for a Fallout 1 character.

    Args:
        character: CharacterInfo with name, description, and optional dialogue
        cache: Optional VoiceCache for caching results
        force: If True, regenerate even if cached

    Returns:
        A concise voice design prompt string for ElevenLabs
    """
    # Skip Ghouls - they use a shared pre-made voice
    if character.creature_type and character.creature_type.lower() == "ghoul":
        return "[Uses shared Ghoul voice]"

    key = character.cache_key()

    # Check cache first
    if cache and not force:
        cached = cache.get(key)
        if cached:
            return cached

    options = ClaudeAgentOptions(
        model="sonnet",
        allowed_tools=["WebSearch"],
        system_prompt=SYSTEM_PROMPT,
        env={"MAX_THINKING_TOKENS" : "2048"}
    )

    user_prompt = f"""Generate a voice design prompt for this Fallout 1 character:

{character.to_prompt()}"""

    result_text = ""

    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    result_text += block.text

    result = extract_voice_prompt(result_text)

    # Ensure we don't exceed ElevenLabs character limit
    result = truncate_to_limit(result)

    # Save to cache
    if cache:
        cache.set(key, result)

    return result


async def generate_voice_prompts_batch(
    characters: list[CharacterInfo],
    cache: VoiceCache | None = None,
    force: bool = False,
) -> dict[str, str]:
    """
    Generate voice prompts for multiple characters.

    Args:
        characters: List of CharacterInfo objects
        cache: Optional VoiceCache for caching results
        force: If True, regenerate even if cached

    Returns:
        Dictionary mapping character names to voice prompts
    """
    results = {}
    for char in characters:
        # Skip Ghouls - they use a shared pre-made voice
        if char.creature_type and char.creature_type.lower() == "ghoul":
            print(f"[ghoul] {char.name} - uses shared Ghoul voice")
            results[char.name] = "[Uses shared Ghoul voice]"
            continue

        key = char.cache_key()
        if cache and not force and key in cache:
            print(f"[cached] {char.name}")
            results[char.name] = cache.get(key)
        else:
            print(f"[generating] {char.name}")
            results[char.name] = await generate_voice_prompt(char, cache, force)
    return results


# CLI interface
async def main():
    # Parse --force flag
    args = sys.argv[1:]
    force = "--force" in args
    if force:
        args.remove("--force")

    if len(args) < 1:
        print("Usage: python voice_designer.py [--force] <character_name> [description]")
        print("       python voice_designer.py [--force] --json <json_file>")
        print("       python voice_designer.py [--force] --npc <npc_dialogue.json> <npc_key>")
        print("       python voice_designer.py [--force] --npc <npc_dialogue.json> --all")
        print()
        print("Options:")
        print("  --force    Regenerate even if cached")
        print()
        print("Example:")
        print('  python voice_designer.py "Killian Darkwater" "Owner of Darkwaters General Store in Junktown"')
        print('  python voice_designer.py --npc tools/npc_dialogue.json killian')
        print('  python voice_designer.py --npc tools/npc_dialogue.json --all')
        print('  python voice_designer.py --force --npc tools/npc_dialogue.json killian')
        print()
        print("JSON file format:")
        print('  {"name": "Character", "description": "...", "dialogue_samples": ["line1", "line2"]}')
        print()
        print("NPC dialogue format (from npc_dialogue.json):")
        print('  {"dialogue": {"npc_key": {"voice_info": {...}, "npc_lines": [...]}}}')
        print()
        print(f"Cache file: {DEFAULT_CACHE_FILE}")
        sys.exit(1)

    # Initialize cache
    cache = VoiceCache()

    if args[0] == "--npc":
        # Load NPC from npc_dialogue.json format
        if len(args) < 2:
            print("Error: --npc requires a JSON file path")
            sys.exit(1)

        with open(args[1], 'r') as f:
            data = json.load(f)

        dialogue = data.get("dialogue", data)

        if len(args) < 3:
            print("Error: --npc requires an NPC key or --all")
            print(f"Available NPCs: {', '.join(list(dialogue.keys())[:20])}...")
            sys.exit(1)

        if args[2] == "--all":
            # Process all NPCs
            characters = [
                CharacterInfo.from_npc_entry(key, entry)
                for key, entry in dialogue.items()
            ]
            results = await generate_voice_prompts_batch(characters, cache, force)
            for name, prompt in results.items():
                print(f"\n=== {name} ===")
                print(prompt)
        else:
            # Process single NPC
            npc_key = args[2].lower()
            if npc_key not in dialogue:
                print(f"Error: NPC '{npc_key}' not found")
                print(f"Available NPCs: {', '.join(list(dialogue.keys())[:20])}...")
                sys.exit(1)

            char = CharacterInfo.from_npc_entry(npc_key, dialogue[npc_key])
            cached = cache.get(char.cache_key())
            if cached and not force:
                print("[cached]")
                print(cached)
            else:
                print("[generating]")
                result = await generate_voice_prompt(char, cache, force)
                print(result)

    elif args[0] == "--json":
        # Load from JSON file
        with open(args[1], 'r') as f:
            data = json.load(f)

        if isinstance(data, list):
            characters = [CharacterInfo(**c) for c in data]
            results = await generate_voice_prompts_batch(characters, cache, force)
            for name, prompt in results.items():
                print(f"\n=== {name} ===")
                print(prompt)
        else:
            char = CharacterInfo(**data)
            cached = cache.get(char.cache_key())
            if cached and not force:
                print("[cached]")
                print(cached)
            else:
                print("[generating]")
                result = await generate_voice_prompt(char, cache, force)
                print(result)
    else:
        # Simple CLI usage
        name = args[0]
        description = args[1] if len(args) > 1 else ""

        char = CharacterInfo(name=name, description=description)
        cached = cache.get(char.cache_key())
        if cached and not force:
            print("[cached]")
            print(cached)
        else:
            print("[generating]")
            result = await generate_voice_prompt(char, cache, force)
            print(result)


if __name__ == "__main__":
    asyncio.run(main())

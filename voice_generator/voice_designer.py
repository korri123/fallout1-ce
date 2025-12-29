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


def extract_voice_prompt(text: str) -> str:
    """
    Extract just the voice design prompt from LLM output,
    stripping preamble, explanations, and source citations.
    """
    # Remove common preamble patterns
    preamble_patterns = [
        r"^.*?voice design prompt[:\.]?\s*",  # "Here's the voice design prompt:"
        r"^.*?I'll search.*?\.\s*",  # "I'll search for more information..."
        r"^.*?Based on.*?:\s*",  # "Based on the wiki information:"
        r"^.*?Here is.*?:\s*",  # "Here is the voice prompt:"
    ]

    result = text.strip()

    for pattern in preamble_patterns:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.DOTALL)

    # Remove Sources section at the end
    result = re.split(r"\n\s*Sources?:", result, flags=re.IGNORECASE)[0]

    # Remove any trailing markdown links
    result = re.sub(r"\n\s*-?\s*\[.*?\]\(.*?\)\s*$", "", result, flags=re.MULTILINE)

    return result.strip()

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

        # Get dialogue samples from voice_info first, fall back to npc_lines
        dialogue_samples = voice_info.get("sample_lines", [])
        if not dialogue_samples:
            npc_lines = entry.get("npc_lines", [])
            dialogue_samples = [line["text"] for line in npc_lines[:10]]

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
            for line in self.dialogue_samples[:10]:  # Limit to 10 samples
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


SYSTEM_PROMPT = """You are a voice design specialist creating prompts for ElevenLabs text-to-speech.

Your task: Given a Fallout 1 character, generate a concise voice design prompt.

PROCESS:
1. Analyze the character's role, personality, and dialogue style
2. To make sure that the generated voice provides realistic delivery, 
3. Generate a voice design prompt using ONLY these characteristics (include only what's relevant):

CHARACTERISTICS TO CONSIDER:
- Age: Young, younger, adult, old, elderly, in his/her 40s, etc.
- Accent: "thick" Scottish, "slight" Asian-American, Southern American, etc.
- Gender: Male, female, gender-neutral, ambiguous
- Tone/Timbre: Deep, warm, gravelly, smooth, shrill, buttery, raspy, nasally, throaty, harsh, robotic, ethereal
- Pacing: Normal cadence, fast-paced, conversational
- Audio Quality: Perfect audio quality (for clear voices), slightly degraded (for radio/intercom)
- Character/Profession: Soldier, merchant, scientist, raider, tribal elder, etc.
- Emotion: Energetic, excited, sad, sarcastic, dry, weary, menacing
- Pitch: Low-pitched, high-pitched, normal pitch

OTHER THINGS TO REMEMBER:
- The voice prompt SHOULD NEVER BE SLOW, we do not want slow boring dialogue. Add to the prompt that the voice should be at least modestly fast paced.
- ALWAYS add delivery info, so that we do not end up with NARRATOR like voices, we want VOICE ACTING
- Don't confuse the model by including "Peasant" or "Berserker", these are Fallout specific terms it doesn't know.
- "Children" usually means Children of the Cathedral, a faction, rather than actual child speaking. There might be child characters though, just don't get them confused. 

OUTPUT FORMAT:
Return the voice design prompt as a single paragraph, 2-4 sentences max.
Example: "A gravelly, deep male voice in his 50s. Speaks with a weary, battle-worn tone. Slight Western American accent with dry, sardonic delivery."

Do NOT include explanations, just the voice prompt."""


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
    key = character.cache_key()

    # Check cache first
    if cache and not force:
        cached = cache.get(key)
        if cached:
            return cached

    options = ClaudeAgentOptions(
        model="haiku",
        allowed_tools=[],
        system_prompt=SYSTEM_PROMPT,
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

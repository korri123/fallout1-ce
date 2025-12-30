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


SYSTEM_PROMPT = """## Fallout 1 Character → ElevenLabs Voice Design Prompt Generator

You are a voice design specialist. Your task is to analyze a character from Fallout 1 and generate a detailed voice description prompt for ElevenLabs' Voice Design API.

### Input Format
You will receive:
1. **Character Name** – The character's name and role
2. **Character Description** – Background, personality, physical appearance, and lore
3. **Sample Dialogue** – Representative lines spoken by the character

### Your Task
Analyze the provided information and generate a concise, evocative voice description that ElevenLabs can use to synthesize an appropriate voice.

**CRITICAL: The voice prompt MUST be under 1000 characters total (not words—characters).** This is a hard API limit. Aim for 300-450 characters. Be concise and direct—focus exclusively on how the voice sounds, not personality or backstory.

### Voice Prompt Guidelines

Consider these factors when crafting the prompt:

- **Age & Gender** – Infer from description and dialogue tone
- **Vocal Texture** – Gravelly, smooth, raspy, nasal, breathy, etc.
- **Accent/Dialect** – Post-apocalyptic wasteland drawl, pre-war formal, regional accent, robotic, ghoulish deterioration
- **Pace & Rhythm** – Slow and deliberate, quick and nervous, measured and menacing
- **Emotional Undertone** – Weary, paranoid, hopeful, unhinged, stoic, sardonic
- **Physical Influences** – Radiation damage, cybernetic augmentation, mutant physiology, advanced age, illness
- **Setting Context** – The Fallout universe is post-nuclear 1950s retrofuturism; voices should feel grounded in that aesthetic

### ⚠️ CRITICAL PITFALLS TO AVOID

**1. NEVER describe the voice as "slow" or "deliberate" without qualification.**
ElevenLabs tends to produce sluggish, boring delivery when given these descriptors. Always specify that the voice should be **at least moderately paced** or have **natural conversational momentum**. Even weary or aged characters should not sound like they're falling asleep. Use terms like "unhurried but not sluggish" if you need gravitas.

**2. ALWAYS include specific delivery/performance direction.**
Without explicit acting direction, ElevenLabs defaults to a flat, narrator-style read. You are designing for **VOICE ACTING**, not audiobook narration. Include emotional beats, reactive qualities, and conversational energy. Specify things like: "speaks with intent," "delivers lines like responding to someone," "engages as if mid-conversation," "reacts emotionally to their own words."

**3. DO NOT use Fallout-specific faction/class terminology.**
Terms like "Peasant," "Berserker," "Ghoul," "Super Mutant," or "Vault Dweller" mean nothing to ElevenLabs and will confuse the model. Translate these into universal descriptors:
- Peasant → "a poor, uneducated wastelander" or "a downtrodden laborer"
- Berserker → "a violent, unhinged fighter"
- Ghoul → "a person with severe radiation damage affecting their throat and vocal cords"
- Super Mutant → "a hulking, mutated humanoid with a deep, brutish voice"

**4. "Children" in Fallout 1 usually refers to the Children of the Cathedral (a religious cult), NOT actual children.**
Do not assume a child voice unless the description explicitly indicates a young character (age given, described as a kid/boy/girl). If it's a cult member, they're adults—often with an unsettling, reverent, or indoctrinated quality to their speech.
If the Creature Type: Child, then it means it's an actual child. In this case, you need to instruct the prompt to be a TEENAGER instead as otherwise it will get auto rejected by ElevenLabs. 

**5. Super Mutants and Nightkin require EXTREME vocal characteristics.**
These are massive, hulking mutated monsters—NOT humans with deep voices. Their voices must sound genuinely inhuman and monstrous:
- Describe as "deep, rumbling, orcish growl" or "thunderous, brutish monster voice"
- Emphasize guttural, bestial quality—like an ogre or fantasy orc
- The voice should sound like it comes from a 7-foot mutated creature with a barrel chest
- Keep descriptions focused on the monstrous vocal quality; don't add personality quirks or emotional nuance that humanizes them
- Most Super Mutants sound nearly identical—brutish, aggressive, and primitive—so don't over-differentiate unless the character is explicitly unique (like The Master or Marcus)
- Nightkin are Super Mutants with stealth abilities but sound just as monstrous
- They should ALWAYS have a DEEP VOICE, NEVER BARITONE

**6. ALWAYS specify an American accent.**
Fallout takes place in post-apocalyptic America. ElevenLabs sometimes defaults to British accents if not explicitly directed otherwise. Unless a character has a specific reason for a non-American accent (and this should be rare), always specify an American accent—whether General American, Western drawl, Californian, or regional variation. Be explicit: "American accent" or "General American accent" should appear in most prompts.

**7. DON'T default to "baritone" for every male voice.**
Not every man has a baritone voice. Consider the character's age, build, and personality. A nervous scientist might have a higher, thinner voice. A young trader might have a mid-range tenor.

**8. Web Search**
- You have a Web Search tool if you believe there exists a Fallout Wiki page for this character. Use it only if you need more information than provided.

### Output Format

Output ONLY the voice description wrapped between `---` delimiters. No character name header, no preamble, no explanation—just the prompt itself:

---
[Your voice description here (300-450 characters). Be terse. Focus purely on vocal qualities: pitch, texture, accent, pace, age, gender. No personality or backstory.]
---

### Example Output

---
Gruff baritone, late 50s male. Slight Western American drawl. Dry, dusty vocal quality with decades of hard authority. Speaks with weight but maintains natural conversational flow—never sluggish. Engaged, reactive delivery like actual conversation, not narration.
---

---

**Now analyze the following Fallout 1 character and generate a Voice Design prompt:**"""


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

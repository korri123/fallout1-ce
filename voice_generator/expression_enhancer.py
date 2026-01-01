"""
Expression Enhancer for Fallout 1 Voice Synthesis

Uses Claude Haiku (via Agent SDK) to add expressive audio tags to dialogue lines
for more natural ElevenLabs v3 synthesis.

Tags like [angrily], [whispering], [laughing] etc. help the TTS
model deliver more emotionally appropriate performances.
"""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

# Default paths
DEFAULT_CACHE_FILE = Path(__file__).parent / "enhanced_dialogue_cache.json"
DEFAULT_VOICE_CACHE = Path(__file__).parent / "voice_cache.json"
DEFAULT_NPC_DIALOGUE = Path(__file__).parent.parent / "tools" / "npc_dialogue.json"
DEFAULT_EXTRASPEECH_DIR = Path(__file__).parent / "extraspeech"

# Cache version - increment to invalidate all cached entries
CACHE_VERSION = 1


@dataclass
class CharacterContext:
    """Context about a character for expression enhancement."""
    name: str
    voice_description: str
    creature_type: str = ""
    gender: str = ""
    faction: str = ""
    appearance: str = ""


class EnhancedDialogueCache:
    """
    Persistent cache for enhanced dialogue lines.

    Cache key format: {npc_key}:{line_id}
    Includes version for cache invalidation.
    """

    def __init__(self, cache_file: Path = DEFAULT_CACHE_FILE):
        self.cache_file = cache_file
        self._cache: dict = {"version": CACHE_VERSION, "entries": {}}
        self._load()

    def _load(self):
        if self.cache_file.exists():
            with open(self.cache_file, 'r') as f:
                data = json.load(f)
                # Invalidate if version mismatch
                if data.get("version") != CACHE_VERSION:
                    print(f"[cache] Version mismatch, clearing cache")
                    self._cache = {"version": CACHE_VERSION, "entries": {}}
                else:
                    self._cache = data

    def _save(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self._cache, f, indent=2)

    def _make_key(self, npc_key: str, line_id: int) -> str:
        return f"{npc_key}:{line_id}"

    def get(self, npc_key: str, line_id: int) -> str | None:
        key = self._make_key(npc_key, line_id)
        return self._cache["entries"].get(key)

    def set(self, npc_key: str, line_id: int, enhanced_text: str):
        key = self._make_key(npc_key, line_id)
        self._cache["entries"][key] = enhanced_text
        self._save()

    def set_batch(self, npc_key: str, line_enhancements: dict[int, str]):
        """Set multiple lines at once, saving only once."""
        for line_id, enhanced_text in line_enhancements.items():
            key = self._make_key(npc_key, line_id)
            self._cache["entries"][key] = enhanced_text
        self._save()

    def has_all_lines(self, npc_key: str, line_ids: list[int]) -> bool:
        """Check if all lines for an NPC are cached."""
        return all(
            self._make_key(npc_key, line_id) in self._cache["entries"]
            for line_id in line_ids
        )

    def clear_npc(self, npc_key: str):
        """Clear all cached entries for an NPC."""
        keys_to_remove = [k for k in self._cache["entries"] if k.startswith(f"{npc_key}:")]
        for key in keys_to_remove:
            del self._cache["entries"][key]
        self._save()


class ExpressionEnhancer:
    """
    Enhances dialogue text with expressive audio tags using Claude Haiku.

    Example transformations:
    - "You killed him!" → "[sobbing] You killed him!"
    - "Get out of here!" → "[angrily] Get out of here!"
    - "Well, well, well..." → "[menacingly, with a slow drawl] Well, well, well..."
    """

    SYSTEM_PROMPT = """You are an expert dialogue director for video game voice acting. Your job is to add expressive audio tags to dialogue lines to help text-to-speech systems deliver emotionally appropriate performances.

## Audio Tag Format
Add tags in square brackets before or within dialogue lines. Examples:
- [angrily] Get out of here!
- [whispering] I think they're coming...
- [laughing] Oh, that's rich!
- [nervously] I... I don't know what you mean.
- [with menacing calm] You have no idea what you've done.
- [sighing] Fine, have it your way.
- [growling] Human. Die.

## Guidelines
1. Tags should reflect the character's personality, species, and emotional state
2. Use specific, evocative descriptions - not just basic emotions
3. Consider the context of the conversation and character's situation
4. For non-human characters (mutants, ghouls, etc.), include appropriate vocal qualities
5. Tags can go mid-sentence for emphasis: "I told you [through gritted teeth] to leave."
6. Multiple short tags are better than one long compound tag
7. You are free to fix typos, add commas and make fixes where the developers made mistakes.
8. If you notice text is clearly not part of the dialogue, such as "Mrs. Jackie hands you the disk", remove it.

## Common Tags
Emotions: [angrily], [sadly], [nervously], [excitedly], [fearfully], [dismissively]
Delivery: [whispering], [shouting], [muttering], [drawling], [quickly], [slowly]
Actions: [sighing], [laughing], [scoffing], [growling], [coughing], [sobbing]
Tone: [sarcastically], [mockingly], [threateningly], [pleadingly], [coldly]
Character-specific: [with a gravelly rumble], [in a raspy croak], [with brutish slowness]

## Stage Directions
Some dialogue contains existing bracketed stage directions describing actions, not speech.
Examples: "[She looks down and shakes her head slowly.]", "[He sighs deeply]", "[Pauses]"
These should be REPLACED with appropriate audio tags that convey the same emotion/action.
- "[She looks down and shakes her head slowly.]" → [sadly, shaking head]
- "[He sighs deeply]" → [sighing deeply]
- "[Pauses]" → [with a pause]
- "[She laughs nervously]" → [laughing nervously]
Do NOT keep the original stage direction - transform it into a speakable audio tag."""

    def __init__(
        self,
        cache_file: Path = DEFAULT_CACHE_FILE,
        voice_cache_file: Path = DEFAULT_VOICE_CACHE,
        npc_dialogue_file: Path = DEFAULT_NPC_DIALOGUE,
        extraspeech_dir: Path = DEFAULT_EXTRASPEECH_DIR,
    ):
        self.cache = EnhancedDialogueCache(cache_file)
        self.voice_cache_file = voice_cache_file
        self.npc_dialogue_file = npc_dialogue_file
        self.extraspeech_dir = extraspeech_dir

        # Loaded data
        self._voice_descriptions: dict[str, str] = {}
        self._dialogue_data: dict = {}

    def _audio_file_exists(self, npc_key: str, line_id: int) -> bool:
        """Check if audio file already exists for this line."""
        audio_path = self.extraspeech_dir / npc_key.lower() / f"{line_id}.mp3"
        return audio_path.exists()

    def _get_lines_without_audio(self, npc_key: str, lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
        """Filter out lines that already have audio files."""
        return [(lid, text) for lid, text in lines if not self._audio_file_exists(npc_key, lid)]

    async def _query_claude(self, prompt: str) -> str:
        """Query Claude Haiku via Agent SDK."""
        options = ClaudeAgentOptions(
            model="sonnet",
            allowed_tools=[],
            system_prompt=self.SYSTEM_PROMPT,
        )

        result_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text

        return result_text

    def _load_voice_descriptions(self) -> dict[str, str]:
        if not self._voice_descriptions:
            with open(self.voice_cache_file, 'r') as f:
                self._voice_descriptions = json.load(f)
        return self._voice_descriptions

    def _load_dialogue(self) -> dict:
        if not self._dialogue_data:
            with open(self.npc_dialogue_file, 'r') as f:
                data = json.load(f)
                self._dialogue_data = data.get("dialogue", data)
        return self._dialogue_data

    def get_character_context(self, npc_key: str) -> CharacterContext:
        """Build character context from available data."""
        voice_descriptions = self._load_voice_descriptions()
        dialogue_data = self._load_dialogue()

        voice_desc = voice_descriptions.get(npc_key.lower(), "")

        npc_data = dialogue_data.get(npc_key.lower(), {})
        voice_info = npc_data.get("voice_info", {})

        return CharacterContext(
            name=npc_key,
            voice_description=voice_desc,
            creature_type=voice_info.get("creature_type", ""),
            gender=voice_info.get("gender", ""),
            faction=voice_info.get("faction", ""),
            appearance=voice_info.get("appearance", ""),
        )

    def _build_enhancement_prompt(
        self,
        character: CharacterContext,
        lines: list[tuple[int, str]],
    ) -> str:
        """Build the prompt for Claude to enhance dialogue lines."""

        # Build character description
        char_parts = [f"Character: {character.name}"]
        if character.gender:
            char_parts.append(f"Gender: {character.gender}")
        if character.creature_type:
            char_parts.append(f"Species/Type: {character.creature_type}")
        if character.faction:
            char_parts.append(f"Faction: {character.faction}")
        if character.appearance:
            char_parts.append(f"Appearance: {character.appearance}")
        if character.voice_description:
            char_parts.append(f"Voice: {character.voice_description}")

        char_block = "\n".join(char_parts)

        # Build lines block
        lines_block = "\n".join(f"[{line_id}] {text}" for line_id, text in lines)

        return f"""## Character Information
{char_block}

## Dialogue Lines to Enhance
Add appropriate expressive audio tags to each line. Return ONLY the enhanced lines in the exact same format: [id] enhanced text

{lines_block}

## Enhanced Lines"""

    def _parse_enhanced_lines(self, response: str) -> dict[int, str]:
        """Parse Claude's response into a dict of line_id -> enhanced_text."""
        results = {}
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            # Parse format: [id] text
            if line.startswith("["):
                bracket_end = line.find("]")
                if bracket_end > 0:
                    try:
                        line_id = int(line[1:bracket_end])
                        text = line[bracket_end + 1:].strip()
                        results[line_id] = text
                    except ValueError:
                        continue

        return results

    def enhance_npc_dialogue(
        self,
        npc_key: str,
        force_refresh: bool = False,
    ) -> dict[int, str]:
        """
        Enhance all dialogue lines for an NPC.

        Args:
            npc_key: NPC identifier
            force_refresh: If True, ignore cache and re-enhance

        Returns:
            Dict mapping line_id to enhanced text
        """
        dialogue_data = self._load_dialogue()

        if npc_key.lower() not in dialogue_data:
            raise KeyError(f"NPC '{npc_key}' not found in dialogue data")

        npc_data = dialogue_data[npc_key.lower()]
        lines = [(line["id"], line["text"]) for line in npc_data.get("npc_lines", [])]

        # Filter out lines that already have audio files
        lines_needing_enhancement = self._get_lines_without_audio(npc_key, lines)

        if not lines_needing_enhancement:
            print(f"[skip] All {len(lines)} lines for {npc_key} already have audio files")
            return {}

        if len(lines_needing_enhancement) < len(lines):
            skipped = len(lines) - len(lines_needing_enhancement)
            print(f"[skip] {skipped} lines for {npc_key} already have audio files")

        # Check cache - separate cached vs uncached lines
        cached_results = {}
        uncached_lines = []
        for line_id, text in lines_needing_enhancement:
            cached = self.cache.get(npc_key, line_id)
            if cached and not force_refresh:
                cached_results[line_id] = cached
            else:
                uncached_lines.append((line_id, text))

        # If all remaining lines are cached, return early
        if not uncached_lines:
            print(f"[cache] All remaining lines for {npc_key} already enhanced")
            return cached_results

        print(f"[cache] {len(cached_results)} cached, {len(uncached_lines)} need enhancement")

        # Get character context
        character = self.get_character_context(npc_key)

        # Check if we have a voice description
        if not character.voice_description:
            print(f"[warn] No voice description for {npc_key}, skipping enhancement")
            # Return cached + original text for uncached
            return {**cached_results, **{lid: text for lid, text in uncached_lines}}

        # Build prompt and call Claude via Agent SDK - only for uncached lines
        prompt = self._build_enhancement_prompt(character, uncached_lines)

        print(f"[enhance] Enhancing {len(uncached_lines)} lines for {npc_key}...")

        # Use async query via Agent SDK
        # Handle both running inside and outside an existing event loop
        try:
            loop = asyncio.get_running_loop()
            # We're inside an existing event loop - create a new thread to run our coroutine
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self._query_claude(prompt))
                response_text = future.result()
        except RuntimeError:
            # No running event loop - we can use asyncio.run directly
            response_text = asyncio.run(self._query_claude(prompt))

        # Parse response
        enhanced = self._parse_enhanced_lines(response_text)

        # Fill in any missing lines with original text (only for uncached lines)
        for line_id, original_text in uncached_lines:
            if line_id not in enhanced:
                print(f"[warn] Line {line_id} not in response, using original")
                enhanced[line_id] = original_text

        # Cache only the newly enhanced results
        self.cache.set_batch(npc_key, enhanced)
        print(f"[cache] Cached {len(enhanced)} newly enhanced lines for {npc_key}")

        # Merge cached results with newly enhanced results
        return {**cached_results, **enhanced}

    def get_enhanced_line(
        self,
        npc_key: str,
        line_id: int,
        original_text: str,
    ) -> str:
        """
        Get enhanced text for a single line.

        If not cached, enhances all lines for the NPC (batch is more efficient).
        """
        # Check cache first
        cached = self.cache.get(npc_key, line_id)
        if cached:
            return cached

        # Enhance all lines for this NPC (batch processing)
        enhanced = self.enhance_npc_dialogue(npc_key)
        return enhanced.get(line_id, original_text)

    def clear_cache(self, npc_key: str | None = None):
        """Clear cached enhancements."""
        if npc_key:
            self.cache.clear_npc(npc_key)
            print(f"[cache] Cleared cache for {npc_key}")
        else:
            self.cache._cache = {"version": CACHE_VERSION, "entries": {}}
            self.cache._save()
            print("[cache] Cleared all cached enhancements")


# CLI interface
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Enhance Fallout 1 dialogue with expressive audio tags"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # enhance command
    enhance_parser = subparsers.add_parser("enhance", help="Enhance dialogue for an NPC")
    enhance_parser.add_argument("npc", help="NPC name/key")
    enhance_parser.add_argument("--force", action="store_true", help="Force refresh (ignore cache)")
    enhance_parser.add_argument("--show", action="store_true", help="Print enhanced lines")

    # enhance-all command
    all_parser = subparsers.add_parser("enhance-all", help="Enhance dialogue for all NPCs with voice descriptions")
    all_parser.add_argument("--force", action="store_true", help="Force refresh")

    # clear-cache command
    clear_parser = subparsers.add_parser("clear-cache", help="Clear enhancement cache")
    clear_parser.add_argument("--npc", help="Clear only for specific NPC")

    # show command
    show_parser = subparsers.add_parser("show", help="Show enhanced dialogue for an NPC")
    show_parser.add_argument("npc", help="NPC name/key")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    enhancer = ExpressionEnhancer()

    if args.command == "enhance":
        enhanced = enhancer.enhance_npc_dialogue(args.npc, force_refresh=args.force)
        print(f"\nEnhanced {len(enhanced)} lines for {args.npc}")

        if args.show:
            print("\n--- Enhanced Dialogue ---")
            for line_id, text in sorted(enhanced.items()):
                print(f"[{line_id}] {text}")

    elif args.command == "enhance-all":
        voice_descriptions = enhancer._load_voice_descriptions()
        dialogue_data = enhancer._load_dialogue()

        # Find NPCs with both voice descriptions and dialogue
        npcs = [npc for npc in voice_descriptions.keys() if npc in dialogue_data]

        print(f"Enhancing {len(npcs)} NPCs...")
        for npc_key in npcs:
            try:
                enhanced = enhancer.enhance_npc_dialogue(npc_key, force_refresh=args.force)
                print(f"  {npc_key}: {len(enhanced)} lines")
            except Exception as e:
                print(f"  {npc_key}: ERROR - {e}")

    elif args.command == "clear-cache":
        enhancer.clear_cache(args.npc)

    elif args.command == "show":
        dialogue_data = enhancer._load_dialogue()
        if args.npc.lower() not in dialogue_data:
            print(f"Error: NPC '{args.npc}' not found")
            return

        npc_data = dialogue_data[args.npc.lower()]
        lines = npc_data.get("npc_lines", [])

        print(f"--- Dialogue for {args.npc} ({len(lines)} lines) ---\n")
        for line in lines:
            line_id = line["id"]
            original = line["text"]
            enhanced = enhancer.cache.get(args.npc, line_id)

            print(f"[{line_id}] Original: {original}")
            if enhanced and enhanced != original:
                print(f"     Enhanced: {enhanced}")
            print()


if __name__ == "__main__":
    main()

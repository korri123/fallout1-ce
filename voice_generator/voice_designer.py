"""
Voice Designer for Fallout 1 Characters

Uses Claude Agent SDK with Haiku to generate ElevenLabs voice design prompts
based on character descriptions and optional web research.
"""

import asyncio
from dataclasses import dataclass
from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock


@dataclass
class CharacterInfo:
    """Information about a Fallout 1 character."""
    name: str
    description: str = ""
    dialogue_samples: list[str] | None = None

    def to_prompt(self) -> str:
        """Format character info for the prompt."""
        parts = [f"Character Name: {self.name}"]

        if self.description:
            parts.append(f"Description: {self.description}")

        if self.dialogue_samples:
            parts.append("Sample Dialogue:")
            for line in self.dialogue_samples[:10]:  # Limit to 10 samples
                parts.append(f'  - "{line}"')

        return "\n".join(parts)


SYSTEM_PROMPT = """You are a voice design specialist creating prompts for ElevenLabs text-to-speech.

Your task: Given a Fallout 1 character, generate a concise voice design prompt.

PROCESS:
1. If a wiki page might exist for this character, search for "Fallout 1 [character name] wiki" to learn more
2. Analyze the character's role, personality, and dialogue style
3. Generate a voice design prompt using ONLY these characteristics (include only what's relevant):

CHARACTERISTICS TO CONSIDER:
- Age: Young, younger, adult, old, elderly, in his/her 40s, etc.
- Accent: "thick" Scottish, "slight" Asian-American, Southern American, etc.
- Gender: Male, female, gender-neutral, ambiguous
- Tone/Timbre: Deep, warm, gravelly, smooth, shrill, buttery, raspy, nasally, throaty, harsh, robotic, ethereal
- Pacing: Normal cadence, fast-paced, slowly, drawn out, calm pace, conversational
- Audio Quality: Perfect audio quality (for clear voices), slightly degraded (for radio/intercom)
- Character/Profession: Soldier, merchant, scientist, raider, tribal elder, etc.
- Emotion: Energetic, excited, sad, sarcastic, dry, weary, menacing
- Pitch: Low-pitched, high-pitched, normal pitch

OUTPUT FORMAT:
Return ONLY the voice design prompt as a single paragraph, 2-4 sentences max.
Example: "A gravelly, deep male voice in his 50s. Speaks slowly with a weary, battle-worn tone. Slight Western American accent with dry, sardonic delivery."

Do NOT include explanations, just the voice prompt."""


async def generate_voice_prompt(character: CharacterInfo) -> str:
    """
    Generate an ElevenLabs voice design prompt for a Fallout 1 character.

    Args:
        character: CharacterInfo with name, description, and optional dialogue

    Returns:
        A concise voice design prompt string for ElevenLabs
    """
    options = ClaudeCodeOptions(
        model="haiku",
        allowed_tools=["WebSearch"],
        system_prompt=SYSTEM_PROMPT,
        permission_mode="acceptEdits",
    )

    user_prompt = f"""Generate a voice design prompt for this Fallout 1 character:

{character.to_prompt()}

Search the Fallout wiki if you need more context about this character."""

    result_text = ""

    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    result_text += block.text

    return result_text.strip()


async def generate_voice_prompts_batch(characters: list[CharacterInfo]) -> dict[str, str]:
    """
    Generate voice prompts for multiple characters.

    Args:
        characters: List of CharacterInfo objects

    Returns:
        Dictionary mapping character names to voice prompts
    """
    results = {}
    for char in characters:
        print(f"Generating voice prompt for: {char.name}")
        results[char.name] = await generate_voice_prompt(char)
    return results


# CLI interface
async def main():
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python voice_designer.py <character_name> [description]")
        print("       python voice_designer.py --json <json_file>")
        print()
        print("Example:")
        print('  python voice_designer.py "Killian Darkwater" "Owner of Darkwaters General Store in Junktown"')
        print()
        print("JSON file format:")
        print('  {"name": "Character", "description": "...", "dialogue_samples": ["line1", "line2"]}')
        sys.exit(1)

    if sys.argv[1] == "--json":
        # Load from JSON file
        with open(sys.argv[2], 'r') as f:
            data = json.load(f)

        if isinstance(data, list):
            characters = [CharacterInfo(**c) for c in data]
            results = await generate_voice_prompts_batch(characters)
            for name, prompt in results.items():
                print(f"\n=== {name} ===")
                print(prompt)
        else:
            char = CharacterInfo(**data)
            result = await generate_voice_prompt(char)
            print(result)
    else:
        # Simple CLI usage
        name = sys.argv[1]
        description = sys.argv[2] if len(sys.argv) > 2 else ""

        char = CharacterInfo(name=name, description=description)
        result = await generate_voice_prompt(char)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())

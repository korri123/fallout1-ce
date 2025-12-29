"""
Unified Voice Generation Pipeline for Fallout 1 Characters

Combines voice_designer.py and voice_synthesizer.py into a single workflow:
1. Generate voice prompt using Claude (via voice_designer)
2. Create ElevenLabs voice from the prompt
3. Synthesize all dialogue lines for the character
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from voice_designer import (
    CharacterInfo,
    VoiceCache,
    generate_voice_prompt,
    DEFAULT_CACHE_FILE,
)
from voice_synthesizer import (
    VoiceSynthesizer,
    DEFAULT_NPC_DIALOGUE,
    DEFAULT_OUTPUT_DIR,
)


class VoiceGenerationPipeline:
    """
    End-to-end voice generation pipeline.

    Workflow:
    1. Load NPC data from npc_dialogue.json
    2. Generate voice design prompt (Claude via voice_designer)
    3. Create voice in ElevenLabs
    4. Synthesize all dialogue lines
    """

    def __init__(
        self,
        npc_dialogue_file: Path = DEFAULT_NPC_DIALOGUE,
        voice_cache_file: Path = DEFAULT_CACHE_FILE,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
    ):
        self.npc_dialogue_file = npc_dialogue_file
        self.voice_cache = VoiceCache(voice_cache_file)
        self.output_dir = output_dir
        self._dialogue_data: dict | None = None
        self._synthesizer: VoiceSynthesizer | None = None

    def _load_dialogue(self) -> dict:
        """Load NPC dialogue data."""
        if self._dialogue_data is None:
            with open(self.npc_dialogue_file, 'r') as f:
                data = json.load(f)
                self._dialogue_data = data.get("dialogue", data)
        return self._dialogue_data

    def _get_synthesizer(self) -> VoiceSynthesizer:
        """Get or create VoiceSynthesizer instance."""
        if self._synthesizer is None:
            self._synthesizer = VoiceSynthesizer(
                npc_dialogue_file=self.npc_dialogue_file,
                output_dir=self.output_dir,
            )
        return self._synthesizer

    def list_npcs(self) -> list[tuple[str, int, bool]]:
        """
        List all available NPCs.

        Returns:
            List of (npc_key, line_count, has_voice_prompt) tuples
        """
        dialogue = self._load_dialogue()
        npcs = []
        for npc_key, npc_data in dialogue.items():
            line_count = len(npc_data.get("npc_lines", []))
            has_prompt = npc_key in self.voice_cache
            npcs.append((npc_key, line_count, has_prompt))
        return sorted(npcs)

    async def generate_prompt(
        self,
        npc_key: str,
        force: bool = False,
    ) -> str:
        """
        Generate a voice design prompt for an NPC.

        Args:
            npc_key: NPC identifier
            force: Regenerate even if cached

        Returns:
            Voice design prompt string
        """
        dialogue = self._load_dialogue()
        npc_key_lower = npc_key.lower()

        if npc_key_lower not in dialogue:
            raise KeyError(f"NPC '{npc_key}' not found in dialogue data")

        # Check cache first
        if not force:
            cached = self.voice_cache.get(npc_key_lower)
            if cached:
                print(f"[cached] Voice prompt for {npc_key}")
                return cached

        # Create CharacterInfo and generate prompt
        char = CharacterInfo.from_npc_entry(npc_key_lower, dialogue[npc_key_lower])
        print(f"[generating] Voice prompt for {char.name}...")

        prompt = await generate_voice_prompt(char, self.voice_cache, force)
        print(f"[done] Generated voice prompt")
        return prompt

    def create_voice(
        self,
        npc_key: str,
        voice_prompt: str | None = None,
        force: bool = False,
    ) -> str:
        """
        Create an ElevenLabs voice for an NPC.

        Args:
            npc_key: NPC identifier
            voice_prompt: Voice design prompt (loaded from cache if not provided)
            force: Recreate even if voice exists

        Returns:
            ElevenLabs voice_id
        """
        synth = self._get_synthesizer()
        npc_key_lower = npc_key.lower()

        # Load prompt from cache if not provided
        if not voice_prompt:
            voice_prompt = self.voice_cache.get(npc_key_lower)
            if not voice_prompt:
                raise ValueError(f"No voice prompt found for '{npc_key}'. Generate one first.")

        return synth.get_or_create_voice(
            name=npc_key_lower,
            description=voice_prompt,
            force_recreate=force,
        )

    def synthesize_dialogue(
        self,
        npc_key: str,
        voice_id: str | None = None,
        max_lines: int | None = None,
    ) -> list[Path]:
        """
        Synthesize dialogue lines for an NPC.

        Args:
            npc_key: NPC identifier
            voice_id: ElevenLabs voice ID (will be looked up if not provided)
            max_lines: Maximum number of lines to synthesize

        Returns:
            List of generated audio file paths
        """
        synth = self._get_synthesizer()
        return synth.synthesize_npc_dialogue(
            npc_key=npc_key.lower(),
            voice_id=voice_id,
            max_lines=max_lines,
        )

    async def run_full_pipeline(
        self,
        npc_key: str,
        force_prompt: bool = False,
        force_voice: bool = False,
        max_lines: int | None = None,
        skip_synthesis: bool = False,
    ) -> dict:
        """
        Run the complete voice generation pipeline.

        Args:
            npc_key: NPC identifier
            force_prompt: Regenerate voice prompt even if cached
            force_voice: Recreate ElevenLabs voice even if exists
            max_lines: Maximum dialogue lines to synthesize
            skip_synthesis: Only generate prompt and create voice, skip TTS

        Returns:
            Dict with 'voice_prompt', 'voice_id', and 'output_files'
        """
        print(f"\n{'='*50}")
        print(f"Voice Generation Pipeline: {npc_key}")
        print(f"{'='*50}\n")

        # Step 1: Generate voice prompt
        print("[Step 1/3] Generating voice prompt...")
        voice_prompt = await self.generate_prompt(npc_key, force=force_prompt)
        print(f"  Prompt: {voice_prompt[:100]}...")

        # Step 2: Create ElevenLabs voice
        print("\n[Step 2/3] Creating ElevenLabs voice...")
        voice_id = self.create_voice(npc_key, voice_prompt, force=force_voice)
        print(f"  Voice ID: {voice_id}")

        # Step 3: Synthesize dialogue
        output_files = []
        if not skip_synthesis:
            print("\n[Step 3/3] Synthesizing dialogue...")
            output_files = self.synthesize_dialogue(npc_key, voice_id, max_lines)
            print(f"  Generated {len(output_files)} audio files")
        else:
            print("\n[Step 3/3] Skipping synthesis (--skip-synthesis)")

        print(f"\n{'='*50}")
        print("Pipeline complete!")
        print(f"{'='*50}\n")

        return {
            "npc_key": npc_key.lower(),
            "voice_prompt": voice_prompt,
            "voice_id": voice_id,
            "output_files": [str(f) for f in output_files],
        }

    async def run_batch_pipeline(
        self,
        npc_keys: list[str] | None = None,
        force_prompt: bool = False,
        force_voice: bool = False,
        max_lines: int | None = None,
        skip_synthesis: bool = False,
    ) -> list[dict]:
        """
        Run pipeline for multiple NPCs.

        Args:
            npc_keys: List of NPC keys (defaults to all with dialogue)
            force_prompt: Regenerate all voice prompts
            force_voice: Recreate all ElevenLabs voices
            max_lines: Max lines per NPC
            skip_synthesis: Only generate prompts and voices

        Returns:
            List of result dicts
        """
        if npc_keys is None:
            npcs = self.list_npcs()
            npc_keys = [npc[0] for npc in npcs if npc[1] > 0]  # Only NPCs with lines

        results = []
        for i, npc_key in enumerate(npc_keys):
            print(f"\n[{i+1}/{len(npc_keys)}] Processing {npc_key}...")
            try:
                result = await self.run_full_pipeline(
                    npc_key=npc_key,
                    force_prompt=force_prompt,
                    force_voice=force_voice,
                    max_lines=max_lines,
                    skip_synthesis=skip_synthesis,
                )
                results.append(result)
            except Exception as e:
                print(f"[error] Failed to process {npc_key}: {e}")
                results.append({
                    "npc_key": npc_key,
                    "error": str(e),
                })

        return results


async def main():
    parser = argparse.ArgumentParser(
        description="Generate voices and synthesize dialogue for Fallout 1 NPCs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate voice and synthesize all dialogue for an NPC
  python generate_voice.py killian

  # Generate voice prompt only (no ElevenLabs API calls)
  python generate_voice.py killian --prompt-only

  # Create voice and synthesize, but limit to 5 lines
  python generate_voice.py killian --max-lines 5

  # Force regenerate everything
  python generate_voice.py killian --force

  # List all available NPCs
  python generate_voice.py --list

  # Process multiple NPCs
  python generate_voice.py killian gizmo aradesh

  # Process all NPCs (careful - uses lots of API credits!)
  python generate_voice.py --all --max-lines 10
""",
    )

    parser.add_argument(
        "npcs",
        nargs="*",
        help="NPC key(s) to process",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available NPCs and exit",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all NPCs with dialogue",
    )
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Only generate voice prompt (no ElevenLabs calls)",
    )
    parser.add_argument(
        "--skip-synthesis",
        action="store_true",
        help="Generate prompt and create voice, but skip TTS synthesis",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        help="Maximum dialogue lines to synthesize per NPC",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regenerate prompt and recreate voice",
    )
    parser.add_argument(
        "--force-prompt",
        action="store_true",
        help="Force regenerate voice prompt only",
    )
    parser.add_argument(
        "--force-voice",
        action="store_true",
        help="Force recreate ElevenLabs voice only",
    )
    parser.add_argument(
        "--dialogue-file",
        type=Path,
        default=DEFAULT_NPC_DIALOGUE,
        help=f"Path to NPC dialogue JSON (default: {DEFAULT_NPC_DIALOGUE})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for audio files (default: {DEFAULT_OUTPUT_DIR})",
    )

    args = parser.parse_args()

    # Create pipeline
    pipeline = VoiceGenerationPipeline(
        npc_dialogue_file=args.dialogue_file,
        output_dir=args.output_dir,
    )

    # Handle --list
    if args.list:
        npcs = pipeline.list_npcs()
        print(f"Available NPCs ({len(npcs)}):\n")
        print(f"{'NPC Key':<20} {'Lines':>6} {'Has Prompt':>12}")
        print("-" * 40)
        for npc_key, line_count, has_prompt in npcs:
            prompt_status = "Yes" if has_prompt else "No"
            print(f"{npc_key:<20} {line_count:>6} {prompt_status:>12}")
        return

    # Handle --prompt-only mode
    if args.prompt_only:
        if not args.npcs and not args.all:
            print("Error: Specify NPC(s) or use --all")
            sys.exit(1)

        npc_keys = args.npcs if args.npcs else None
        if args.all:
            npcs = pipeline.list_npcs()
            npc_keys = [npc[0] for npc in npcs]

        force = args.force or args.force_prompt
        for npc_key in npc_keys:
            try:
                prompt = await pipeline.generate_prompt(npc_key, force=force)
                print(f"\n=== {npc_key} ===")
                print(prompt)
            except Exception as e:
                print(f"[error] {npc_key}: {e}")
        return

    # Determine which NPCs to process
    if args.all:
        npc_keys = None  # Will process all
    elif args.npcs:
        npc_keys = args.npcs
    else:
        parser.print_help()
        return

    # Run pipeline
    force_prompt = args.force or args.force_prompt
    force_voice = args.force or args.force_voice

    if npc_keys and len(npc_keys) == 1:
        # Single NPC
        result = await pipeline.run_full_pipeline(
            npc_key=npc_keys[0],
            force_prompt=force_prompt,
            force_voice=force_voice,
            max_lines=args.max_lines,
            skip_synthesis=args.skip_synthesis,
        )

        # Print summary
        print("\nResult:")
        print(f"  Voice Prompt: {result['voice_prompt']}")
        print(f"  Voice ID: {result['voice_id']}")
        print(f"  Output Files: {len(result['output_files'])}")
    else:
        # Multiple NPCs
        results = await pipeline.run_batch_pipeline(
            npc_keys=npc_keys,
            force_prompt=force_prompt,
            force_voice=force_voice,
            max_lines=args.max_lines,
            skip_synthesis=args.skip_synthesis,
        )

        # Print summary
        print("\n" + "=" * 50)
        print("Batch Summary")
        print("=" * 50)

        success = [r for r in results if "error" not in r]
        failed = [r for r in results if "error" in r]

        print(f"  Successful: {len(success)}")
        print(f"  Failed: {len(failed)}")

        if failed:
            print("\nFailed NPCs:")
            for r in failed:
                print(f"  - {r['npc_key']}: {r['error']}")


if __name__ == "__main__":
    asyncio.run(main())

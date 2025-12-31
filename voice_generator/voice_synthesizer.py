"""
Voice Synthesizer for Fallout 1 Characters

Uses ElevenLabs API to:
1. Create voices from text descriptions (voice_cache.json)
2. Generate speech for dialogue lines (npc_dialogue.json)
"""

import base64
import json
import os
import random
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from elevenlabs import ElevenLabs

from audio_effects import apply_fade_out, normalize_loudness, pitch_shift, DEFAULT_FADE_DURATION_MS, DEFAULT_TARGET_LUFS
from expression_enhancer import ExpressionEnhancer

# Default paths
DEFAULT_VOICE_CACHE = Path(__file__).parent / "voice_cache.json"
DEFAULT_NPC_DIALOGUE = Path(__file__).parent.parent / "tools" / "npc_dialogue.json"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "extraspeech"
DEFAULT_VOICE_IDS_FILE = Path(__file__).parent / "voice_ids.json"
DEFAULT_VOICE_SEEDS_FILE = Path(__file__).parent / "voice_seeds.json"

# Creature-specific audio effects
SUPER_MUTANT_PITCH_FACTOR = 0.9  # 10% lower pitch for Super Mutants

# Shared voice for Ghouls - all ghoul characters use the same voice
GHOUL_VOICE_ID = "KAJMJ4av1R2CVQ10ndCZ"

# Protected voices that should not be deleted
PROTECTED_VOICES = {"Ghoul"}


@dataclass
class VoiceConfig:
    """Configuration for a character's voice."""
    name: str
    description: str
    voice_id: str | None = None
    generated_voice_id: str | None = None  # Temporary preview ID


@dataclass
class DialogueLine:
    """A single dialogue line to be synthesized."""
    npc_key: str
    line_id: int
    text: str


class VoiceIDCache:
    """Persistent cache mapping character names to ElevenLabs voice IDs."""

    def __init__(self, cache_file: Path = DEFAULT_VOICE_IDS_FILE):
        self.cache_file = cache_file
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self):
        if self.cache_file.exists():
            with open(self.cache_file, 'r') as f:
                self._cache = json.load(f)

    def _save(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self._cache, f, indent=2)

    def get(self, name: str) -> str | None:
        return self._cache.get(name.lower())

    def set(self, name: str, voice_id: str):
        self._cache[name.lower()] = voice_id
        self._save()

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._cache

    def items(self):
        return self._cache.items()


class VoiceSeedCache:
    """Persistent cache mapping character names to voice design seeds for reproducibility."""

    def __init__(self, cache_file: Path = DEFAULT_VOICE_SEEDS_FILE):
        self.cache_file = cache_file
        self._cache: dict[str, int] = {}
        self._load()

    def _load(self):
        if self.cache_file.exists():
            with open(self.cache_file, 'r') as f:
                self._cache = json.load(f)

    def _save(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self._cache, f, indent=2, sort_keys=True)

    def get(self, name: str) -> int | None:
        return self._cache.get(name.lower())

    def set(self, name: str, seed: int):
        self._cache[name.lower()] = seed
        self._save()

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._cache

    def items(self):
        return self._cache.items()


class VoiceSynthesizer:
    """
    Synthesizes voices and speech using ElevenLabs API.

    Workflow:
    1. Load voice descriptions from voice_cache.json
    2. Create voices using the Voice Design API
    3. Load dialogue from npc_dialogue.json
    4. Generate speech using Text-to-Speech API
    """

    def __init__(
        self,
        api_key: str | None = None,
        voice_cache_file: Path = DEFAULT_VOICE_CACHE,
        npc_dialogue_file: Path = DEFAULT_NPC_DIALOGUE,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        tts_model_id: str = "eleven_v3",
        voice_design_model_id: str = "eleven_ttv_v3",
        output_format: str = "mp3_44100_128",
        enable_expression_enhancement: bool = True,
    ):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise ValueError("ElevenLabs API key required. Set ELEVENLABS_API_KEY env var or pass api_key.")

        self.client = ElevenLabs(api_key=self.api_key)
        self.voice_cache_file = voice_cache_file
        self.npc_dialogue_file = npc_dialogue_file
        self.output_dir = output_dir
        self.tts_model_id = tts_model_id
        self.voice_design_model_id = voice_design_model_id
        self.output_format = output_format
        self.fade_out_ms = DEFAULT_FADE_DURATION_MS
        self.normalize_lufs = DEFAULT_TARGET_LUFS  # -16 LUFS for consistent volume

        # Voice ID persistence
        self.voice_ids = VoiceIDCache()
        self.voice_seeds = VoiceSeedCache()

        # Expression enhancement (adds audio tags like [angrily], [whispering], etc.)
        self.enable_expression_enhancement = enable_expression_enhancement
        self._expression_enhancer: ExpressionEnhancer | None = None

        # Loaded data
        self._voice_descriptions: dict[str, str] = {}
        self._dialogue_data: dict = {}

    def get_expression_enhancer(self) -> ExpressionEnhancer:
        """Get or create the expression enhancer (lazy initialization)."""
        if self._expression_enhancer is None:
            self._expression_enhancer = ExpressionEnhancer(
                voice_cache_file=self.voice_cache_file,
                npc_dialogue_file=self.npc_dialogue_file,
            )
        return self._expression_enhancer

    def load_voice_descriptions(self) -> dict[str, str]:
        """Load voice descriptions from voice_cache.json."""
        if not self._voice_descriptions:
            with open(self.voice_cache_file, 'r') as f:
                self._voice_descriptions = json.load(f)
        return self._voice_descriptions

    def load_dialogue(self) -> dict:
        """Load dialogue data from npc_dialogue.json."""
        if not self._dialogue_data:
            with open(self.npc_dialogue_file, 'r') as f:
                data = json.load(f)
                self._dialogue_data = data.get("dialogue", data)
        return self._dialogue_data

    def get_npc_lines(self, npc_key: str) -> list[DialogueLine]:
        """Get all dialogue lines for an NPC."""
        dialogue = self.load_dialogue()
        if npc_key not in dialogue:
            raise KeyError(f"NPC '{npc_key}' not found in dialogue data")

        npc_data = dialogue[npc_key]
        lines = []
        for line in npc_data.get("npc_lines", []):
            lines.append(DialogueLine(
                npc_key=npc_key,
                line_id=line["id"],
                text=line["text"],
            ))
        return lines

    def get_creature_type(self, npc_key: str) -> str | None:
        """Get the creature_type from voice_info for an NPC."""
        dialogue = self.load_dialogue()
        if npc_key.lower() not in dialogue:
            return None
        npc_data = dialogue[npc_key.lower()]
        voice_info = npc_data.get("voice_info", {})
        return voice_info.get("creature_type") or None

    def get_pitch_factor(self, npc_key: str) -> float:
        """Get the pitch factor for an NPC based on creature type or appearance."""
        creature_type = self.get_creature_type(npc_key)

        # Check explicit creature_type first
        if creature_type:
            creature_lower = creature_type.lower()
            if creature_lower in ("super mutant", "nightkin"):
                return SUPER_MUTANT_PITCH_FACTOR

        # Fallback: check appearance text if creature_type is blank
        dialogue = self.load_dialogue()
        if npc_key.lower() in dialogue:
            npc_data = dialogue[npc_key.lower()]
            appearance = npc_data.get("voice_info", {}).get("appearance", "").lower()
            if "super mutant" in appearance or "nightkin" in appearance:
                return SUPER_MUTANT_PITCH_FACTOR

        return 1.0

    def get_sample_text(self, npc_key: str, min_length: int = 200, max_length: int = 500) -> str | None:
        """
        Get sample dialogue text for voice design.

        ElevenLabs Voice Design API requires 3-5 sentences, 200-500 characters.
        Combines sample_lines and npc_lines as needed to reach the target length.

        Args:
            npc_key: NPC identifier
            min_length: Minimum text length (API requires ~200)
            max_length: Maximum text length (API recommends ~500)

        Returns:
            Combined sample text, or None if not enough dialogue
        """
        dialogue = self.load_dialogue()
        if npc_key.lower() not in dialogue:
            return None

        npc_data = dialogue[npc_key.lower()]
        voice_info = npc_data.get("voice_info", {})

        # Get sample_lines from voice_info
        sample_lines = voice_info.get("sample_lines", [])

        # Get all npc_lines as backup/supplement
        npc_lines = [line["text"] for line in npc_data.get("npc_lines", [])]

        # Build combined text, starting with sample_lines then adding npc_lines if needed
        combined = ""
        used_texts = set()

        # First pass: use sample_lines
        for line in sample_lines:
            if line in used_texts:
                continue
            used_texts.add(line)

            if combined:
                combined += " "
            combined += line

            # Stop if we've reached max length
            if len(combined) >= max_length:
                break

        # Second pass: if still too short, add from npc_lines
        if len(combined) < min_length:
            for line in npc_lines:
                if line in used_texts:
                    continue
                used_texts.add(line)

                if combined:
                    combined += " "
                combined += line

                # Stop once we reach target range
                if len(combined) >= min_length:
                    break

                # Hard stop at max
                if len(combined) >= max_length:
                    break

        # Truncate if too long, try to break at word boundary
        if len(combined) > max_length:
            combined = combined[:max_length]
            # Find last space to avoid cutting mid-word
            last_space = combined.rfind(" ")
            if last_space > min_length:
                combined = combined[:last_space]

        # Return None if still too short
        if len(combined) < min_length:
            return None

        return combined

    def design_voice(
        self,
        name: str,
        description: str,
        seed: int | None = None,
    ) -> dict:
        """
        Design a voice from a text description.

        Args:
            name: Character name (used for seed storage)
            description: Voice description text
            seed: Optional seed for reproducibility (0-2147483647).
                  If not provided, generates a random seed.

        Returns dict with 'generated_voice_id', 'preview_audio' (base64), and 'seed'.
        """
        # Generate random seed if not provided
        if seed is None:
            seed = random.randint(0, 2147483647)

        # Store seed for reproducibility
        self.voice_seeds.set(name, seed)
        print(f"[design] Designing voice for {name} (seed={seed})...")

        response = self.client.text_to_voice.design(
            model_id=self.voice_design_model_id,
            guidance_scale=4.4,
            loudness=0.5,
            voice_description=description,
            auto_generate_text=True,
            output_format="mp3_22050_32",
            should_enhance=True,
            seed=seed,
        )

        # Get the first preview
        if response.previews and len(response.previews) > 0:
            preview = response.previews[0]
            return {
                "generated_voice_id": preview.generated_voice_id,
                "preview_audio": preview.audio_base_64,
                "seed": seed,
            }

        raise RuntimeError(f"No voice previews generated for {name}")

    def design_voice_multi(
        self,
        name: str,
        description: str,
        seed: int | None = None,
    ) -> list[dict]:
        """
        Design multiple voice previews from a text description.

        Args:
            name: Character name (used for seed storage)
            description: Voice description text
            seed: Optional seed for reproducibility (0-2147483647).
                  If not provided, generates a random seed.

        Returns list of dicts with 'generated_voice_id', 'preview_audio' (base64), and 'seed'.
        """
        # Generate random seed if not provided
        if seed is None:
            seed = random.randint(0, 2147483647)

        # Store seed for reproducibility
        self.voice_seeds.set(name, seed)
        print(f"[design] Designing voices for {name} (seed={seed})...")

        response = self.client.text_to_voice.design(
            model_id=self.voice_design_model_id,
            guidance_scale=4.4,
            loudness=0.5,
            voice_description=description,
            auto_generate_text=True,
            output_format="mp3_22050_32",
            should_enhance=True,
            seed=seed,
        )

        # Get all previews
        if response.previews and len(response.previews) > 0:
            return [
                {
                    "generated_voice_id": preview.generated_voice_id,
                    "preview_audio": preview.audio_base_64,
                    "seed": seed,
                    "index": i + 1,
                }
                for i, preview in enumerate(response.previews)
            ]

        raise RuntimeError(f"No voice previews generated for {name}")

    def update_voice_description(self, name: str, description: str) -> None:
        """
        Update the voice description in voice_cache.json.

        Args:
            name: Character name (key in voice_cache.json)
            description: New voice description text
        """
        # Load current descriptions
        with open(self.voice_cache_file, 'r') as f:
            descriptions = json.load(f)

        # Update the description
        descriptions[name.lower()] = description

        # Save back
        with open(self.voice_cache_file, 'w') as f:
            json.dump(descriptions, f, indent=2)

        # Clear cached descriptions so they get reloaded
        self._voice_descriptions = {}
        print(f"[updated] voice_cache.json updated for {name}")

    def interactive_preview_voice(self, name: str, initial_description: str | None = None) -> dict | None:
        """
        Interactive voice preview workflow.

        Generates 3 voice previews, saves to temp directory, opens in Finder,
        and waits for user selection. If rejected, allows prompt rewriting.

        Args:
            name: Character name
            initial_description: Initial voice description (loaded from cache if not provided)

        Returns:
            Dict with 'generated_voice_id', 'description' (possibly updated), 'seed'
            or None if user cancels
        """
        # Load description if not provided
        if not initial_description:
            descriptions = self.load_voice_descriptions()
            if name.lower() not in descriptions:
                raise KeyError(f"No voice description found for '{name}'")
            initial_description = descriptions[name.lower()]

        current_description = initial_description

        while True:
            # Generate 3 previews
            print(f"\n{'='*60}")
            print(f"Generating voice previews for: {name}")
            print(f"{'='*60}")
            print(f"\nPrompt:\n{current_description}\n")

            previews = self.design_voice_multi(name, current_description)
            print(f"[generated] {len(previews)} voice previews")

            # Create temp directory and save previews
            temp_dir = Path(tempfile.mkdtemp(prefix=f"voice_preview_{name}_"))
            print(f"[temp] Saving previews to: {temp_dir}")

            for preview in previews:
                audio_data = base64.b64decode(preview["preview_audio"])
                preview_path = temp_dir / f"voice_{preview['index']}.mp3"
                with open(preview_path, 'wb') as f:
                    f.write(audio_data)
                print(f"  Saved: {preview_path.name}")

            # Open in Finder
            subprocess.run(["open", str(temp_dir)])
            print(f"\n[finder] Opened preview folder in Finder")

            # Wait for user input
            print(f"\n{'='*60}")
            print("Listen to the 3 voice previews and choose:")
            print("  1, 2, or 3 - Select that voice")
            print("  r          - Reject all and enter new prompt")
            print("  q          - Quit/cancel")
            print(f"{'='*60}")

            while True:
                choice = input("\nYour choice: ").strip().lower()

                if choice in ('1', '2', '3'):
                    idx = int(choice) - 1
                    if idx < len(previews):
                        selected = previews[idx]
                        print(f"\n[selected] Voice {choice}")

                        # Update voice_cache.json if prompt was changed
                        if current_description != initial_description:
                            self.update_voice_description(name, current_description)

                        return {
                            "generated_voice_id": selected["generated_voice_id"],
                            "preview_audio": selected["preview_audio"],
                            "seed": selected["seed"],
                            "description": current_description,
                        }
                    else:
                        print(f"Invalid choice. Only {len(previews)} previews available.")

                elif choice == 'r':
                    # Print current prompt and ask for new one
                    print(f"\n{'='*60}")
                    print("Current prompt (copy and modify):")
                    print(f"{'='*60}")
                    print(current_description)
                    print(f"{'='*60}")
                    print("\nEnter new prompt (paste modified version):")
                    print("(Enter a blank line when done)")

                    lines = []
                    while True:
                        line = input()
                        if line == "":
                            break
                        lines.append(line)

                    if lines:
                        current_description = "\n".join(lines)
                        print("\n[prompt] Updated. Generating new previews...")
                        break  # Break inner loop to regenerate
                    else:
                        print("[cancelled] No prompt entered, keeping current.")

                elif choice == 'q':
                    print("[cancelled] Voice preview cancelled.")
                    return None

                else:
                    print("Invalid choice. Enter 1, 2, 3, r, or q.")

    def create_voice_from_preview(
        self,
        name: str,
        description: str,
        generated_voice_id: str,
    ) -> str:
        """
        Create a permanent voice from a preview.

        Returns the permanent voice_id.
        """
        print(f"[create] Creating permanent voice for {name}...")

        try:
            response = self.client.text_to_voice.create(
                voice_name=name,
                voice_description=description,
                generated_voice_id=generated_voice_id,
            )
        except Exception as e:
            # Check if it's a voice limit error
            error_str = str(e)
            if "voice_limit_reached" in error_str:
                print("[warn] Voice limit reached, deleting all custom voices...")
                self.delete_all_custom_voices()
                print("[retry] Retrying voice creation...")
                response = self.client.text_to_voice.create(
                    voice_name=name,
                    voice_description=description,
                    generated_voice_id=generated_voice_id,
                )
            else:
                raise

        voice_id = response.voice_id
        self.voice_ids.set(name, voice_id)
        print(f"[created] Voice ID: {voice_id}")
        return voice_id

    def get_or_create_voice(
        self,
        name: str,
        description: str | None = None,
        force_recreate: bool = False,
    ) -> str:
        """
        Get existing voice ID or create a new voice.

        Args:
            name: Character name (key in voice_cache.json)
            description: Voice description (loaded from cache if not provided)
            force_recreate: If True, create new voice even if one exists

        Returns:
            ElevenLabs voice_id
        """
        # Check if this is a Ghoul - use shared Ghoul voice
        creature_type = self.get_creature_type(name)
        if creature_type and creature_type.lower() == "ghoul":
            print(f"[ghoul] Using shared Ghoul voice for {name}: {GHOUL_VOICE_ID}")
            return GHOUL_VOICE_ID

        # Check if we already have a voice ID
        if not force_recreate and name in self.voice_ids:
            voice_id = self.voice_ids.get(name)
            print(f"[cached] Using existing voice for {name}: {voice_id}")
            return voice_id

        # Load description if not provided
        if not description:
            descriptions = self.load_voice_descriptions()
            if name.lower() not in descriptions:
                raise KeyError(f"No voice description found for '{name}'")
            description = descriptions[name.lower()]

        # Design and create the voice
        preview = self.design_voice(name, description)
        voice_id = self.create_voice_from_preview(
            name=name,
            description=description,
            generated_voice_id=preview["generated_voice_id"],
        )

        return voice_id

    def synthesize_line(
        self,
        text: str,
        voice_id: str,
        output_path: Path | None = None,
        pitch_factor: float = 1.0,
    ) -> bytes:
        """
        Synthesize a single line of dialogue.

        Args:
            text: The text to speak
            voice_id: ElevenLabs voice ID
            output_path: Optional path to save the audio file
            pitch_factor: Pitch multiplier (0.8 = 20% lower for Super Mutants)

        Returns:
            Audio data as bytes
        """
        audio = self.client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=self.tts_model_id,
            output_format=self.output_format,
            language_code='en',
            voice_settings={
                "stability": 0,
            }
        )

        # Convert generator to bytes
        audio_bytes = b"".join(audio)

        # Apply pitch shift for creature types (e.g., Super Mutants)
        if pitch_factor != 1.0:
            audio_bytes = pitch_shift(audio_bytes, pitch_factor)

        # Apply fade-out effect
        if self.fade_out_ms > 0:
            audio_bytes = apply_fade_out(audio_bytes, self.fade_out_ms)

        # Normalize loudness for consistent volume across all files
        if self.normalize_lufs is not None:
            audio_bytes = normalize_loudness(audio_bytes, self.normalize_lufs)

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(audio_bytes)
            print(f"[saved] {output_path}")

        return audio_bytes

    def synthesize_npc_dialogue(
        self,
        npc_key: str,
        voice_id: str | None = None,
        max_lines: int | None = None,
        enhance: bool | None = None,
    ) -> list[Path]:
        """
        Synthesize all dialogue for an NPC.

        Args:
            npc_key: NPC identifier (e.g., "abel", "agatha")
            voice_id: Optional voice ID (will look up or create if not provided)
            max_lines: Optional limit on number of lines to synthesize
            enhance: Override expression enhancement (None = use default setting)

        Returns:
            List of output file paths
        """
        # Get or create voice
        if not voice_id:
            voice_id = self.get_or_create_voice(npc_key)

        # Get pitch factor based on creature type (e.g., Super Mutants, Nightkin)
        pitch_factor = self.get_pitch_factor(npc_key)
        if pitch_factor != 1.0:
            creature_type = self.get_creature_type(npc_key)
            if creature_type:
                print(f"[pitch] Applying {pitch_factor}x pitch shift for {creature_type}")
            else:
                print(f"[pitch] Applying {pitch_factor}x pitch shift (detected from appearance)")

        # Get dialogue lines
        lines = self.get_npc_lines(npc_key)
        if max_lines:
            lines = lines[:max_lines]

        # Get enhanced text if enabled
        should_enhance = enhance if enhance is not None else self.enable_expression_enhancement
        enhanced_lines: dict[int, str] = {}

        if should_enhance:
            try:
                enhancer = self.get_expression_enhancer()
                enhanced_lines = enhancer.enhance_npc_dialogue(npc_key)
            except Exception as e:
                print(f"[warn] Expression enhancement failed: {e}")
                print("[warn] Falling back to original text")

        # Create output directory for this NPC
        npc_output_dir = self.output_dir / npc_key
        npc_output_dir.mkdir(parents=True, exist_ok=True)

        output_files = []
        skipped = 0
        for i, line in enumerate(lines):
            output_path = npc_output_dir / f"{line.line_id}.mp3"

            if output_path.exists():
                skipped += 1
                output_files.append(output_path)
                continue

            # Use enhanced text if available, otherwise original
            text_to_synthesize = enhanced_lines.get(line.line_id, line.text)

            # Show what we're synthesizing
            display_text = text_to_synthesize[:60] + "..." if len(text_to_synthesize) > 60 else text_to_synthesize
            print(f"[{i+1}/{len(lines)}] Synthesizing line {line.line_id}: {display_text}")

            self.synthesize_line(
                text=text_to_synthesize,
                voice_id=voice_id,
                output_path=output_path,
                pitch_factor=pitch_factor,
            )
            output_files.append(output_path)

        if skipped:
            print(f"[skipped] {skipped} existing files")

        return output_files

    def synthesize_all_npcs(
        self,
        npc_keys: list[str] | None = None,
        max_lines_per_npc: int | None = None,
    ) -> dict[str, list[Path]]:
        """
        Synthesize dialogue for multiple NPCs.

        Args:
            npc_keys: List of NPC keys to process (defaults to all in voice_cache)
            max_lines_per_npc: Optional limit per NPC

        Returns:
            Dict mapping NPC keys to their output file lists
        """
        if npc_keys is None:
            # Use all NPCs that have voice descriptions
            descriptions = self.load_voice_descriptions()
            npc_keys = list(descriptions.keys())

        results = {}
        for npc_key in npc_keys:
            try:
                print(f"\n=== Processing {npc_key} ===")
                results[npc_key] = self.synthesize_npc_dialogue(
                    npc_key=npc_key,
                    max_lines=max_lines_per_npc,
                )
            except Exception as e:
                print(f"[error] Failed to process {npc_key}: {e}")
                results[npc_key] = []

        return results

    def list_available_npcs(self) -> list[str]:
        """List NPCs that have both voice descriptions and dialogue."""
        descriptions = self.load_voice_descriptions()
        dialogue = self.load_dialogue()

        # NPCs with both voice descriptions and dialogue
        available = []
        for npc_key in descriptions.keys():
            if npc_key in dialogue:
                line_count = len(dialogue[npc_key].get("npc_lines", []))
                available.append((npc_key, line_count))

        return available

    def list_voices(self) -> list[dict]:
        """List all voices in the ElevenLabs account."""
        response = self.client.voices.get_all()
        return [
            {"voice_id": v.voice_id, "name": v.name, "category": v.category}
            for v in response.voices
        ]

    def delete_voice(self, voice_id: str) -> bool:
        """Delete a voice by its ID."""
        try:
            self.client.voices.delete(voice_id=voice_id)
            return True
        except Exception as e:
            print(f"[error] Failed to delete voice {voice_id}: {e}")
            return False

    def delete_all_custom_voices(self, dry_run: bool = False) -> int:
        """
        Delete all custom/cloned voices (not premade ones).

        Args:
            dry_run: If True, only print what would be deleted without actually deleting

        Returns:
            Number of voices deleted (or would be deleted in dry_run mode)
        """
        voices = self.list_voices()
        custom_voices = [
            v for v in voices
            if v["category"] not in ("premade", "professional")
            and v["name"] not in PROTECTED_VOICES
        ]

        if not custom_voices:
            print("[info] No custom voices to delete")
            return 0

        print(f"[info] Found {len(custom_voices)} custom voices to delete")

        deleted = 0
        for v in custom_voices:
            if dry_run:
                print(f"[dry-run] Would delete: {v['name']} ({v['voice_id']})")
                deleted += 1
            else:
                print(f"[delete] Deleting: {v['name']} ({v['voice_id']})")
                if self.delete_voice(v["voice_id"]):
                    deleted += 1

        return deleted


# CLI interface
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Synthesize Fallout 1 NPC voices using ElevenLabs"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list-npcs command
    list_parser = subparsers.add_parser("list-npcs", help="List available NPCs")

    # list-voices command
    voices_parser = subparsers.add_parser("list-voices", help="List ElevenLabs voices")

    # delete-voices command
    delete_parser = subparsers.add_parser("delete-voices", help="Delete all custom voices")
    delete_parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")

    # create-voice command
    create_parser = subparsers.add_parser("create-voice", help="Create a voice for an NPC")
    create_parser.add_argument("npc", help="NPC name/key")
    create_parser.add_argument("--force", action="store_true", help="Recreate even if exists")

    # synthesize command
    synth_parser = subparsers.add_parser("synthesize", help="Synthesize dialogue for an NPC")
    synth_parser.add_argument("npc", help="NPC name/key")
    synth_parser.add_argument("--voice-id", help="Override voice ID")
    synth_parser.add_argument("--max-lines", type=int, help="Max lines to synthesize")
    synth_parser.add_argument("--no-enhance", action="store_true", help="Disable expression enhancement")

    # synthesize-all command
    all_parser = subparsers.add_parser("synthesize-all", help="Synthesize all NPCs")
    all_parser.add_argument("--max-lines", type=int, help="Max lines per NPC")
    all_parser.add_argument("--no-enhance", action="store_true", help="Disable expression enhancement")

    # preview command
    preview_parser = subparsers.add_parser("preview", help="Preview a voice design (interactive)")
    preview_parser.add_argument("npc", help="NPC name/key")
    preview_parser.add_argument("--save", help="Save single preview to file (non-interactive mode)")
    preview_parser.add_argument("--create", action="store_true", help="Create permanent voice after selection")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    synth = VoiceSynthesizer()

    if args.command == "list-npcs":
        npcs = synth.list_available_npcs()
        print(f"Available NPCs ({len(npcs)}):")
        for npc_key, line_count in sorted(npcs):
            print(f"  {npc_key}: {line_count} lines")

    elif args.command == "list-voices":
        voices = synth.list_voices()
        print(f"ElevenLabs Voices ({len(voices)}):")
        for v in voices:
            print(f"  {v['name']}: {v['voice_id']} ({v['category']})")

    elif args.command == "delete-voices":
        deleted = synth.delete_all_custom_voices(dry_run=args.dry_run)
        if args.dry_run:
            print(f"\n[dry-run] Would delete {deleted} voices")
        else:
            print(f"\nDeleted {deleted} voices")

    elif args.command == "create-voice":
        voice_id = synth.get_or_create_voice(args.npc, force_recreate=args.force)
        print(f"Voice ID for {args.npc}: {voice_id}")

    elif args.command == "synthesize":
        # Determine enhancement setting
        enhance = None if not args.no_enhance else False

        files = synth.synthesize_npc_dialogue(
            npc_key=args.npc,
            voice_id=args.voice_id,
            max_lines=args.max_lines,
            enhance=enhance,
        )
        print(f"\nSynthesized {len(files)} files for {args.npc}")

    elif args.command == "synthesize-all":
        # Set enhancement based on flag
        if args.no_enhance:
            synth.enable_expression_enhancement = False

        results = synth.synthesize_all_npcs(max_lines_per_npc=args.max_lines)
        print("\n=== Summary ===")
        for npc, files in results.items():
            print(f"  {npc}: {len(files)} files")

    elif args.command == "preview":
        descriptions = synth.load_voice_descriptions()
        if args.npc.lower() not in descriptions:
            print(f"Error: No voice description for '{args.npc}'")
            return

        if args.save:
            # Non-interactive mode: generate single preview and save
            description = descriptions[args.npc.lower()]
            print(f"Description: {description}\n")

            preview = synth.design_voice(args.npc, description)
            print(f"Generated Voice ID: {preview['generated_voice_id']}")

            audio_data = base64.b64decode(preview["preview_audio"])
            with open(args.save, 'wb') as f:
                f.write(audio_data)
            print(f"Saved preview to: {args.save}")
        else:
            # Interactive mode: show 3 previews, allow selection/rejection
            result = synth.interactive_preview_voice(args.npc)

            if result:
                print(f"\n{'='*60}")
                print(f"Selected voice for {args.npc}")
                print(f"Generated Voice ID: {result['generated_voice_id']}")
                print(f"Seed: {result['seed']}")
                print(f"{'='*60}")

                if args.create:
                    # Create permanent voice
                    voice_id = synth.create_voice_from_preview(
                        name=args.npc,
                        description=result['description'],
                        generated_voice_id=result['generated_voice_id'],
                    )
                    print(f"\n[success] Created permanent voice: {voice_id}")


if __name__ == "__main__":
    main()

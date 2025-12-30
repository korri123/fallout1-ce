"""
Audio Effects Module for Fallout 1 Voice Generation

Provides post-processing effects for generated audio files.
Uses ffmpeg directly for Python 3.13+ compatibility.
"""

import subprocess
import tempfile
from pathlib import Path


DEFAULT_FADE_DURATION_MS = 100
DEFAULT_TARGET_LUFS = -16.0


def normalize_loudness(audio_bytes: bytes, target_lufs: float = DEFAULT_TARGET_LUFS) -> bytes:
    """
    Normalize audio to a target loudness level (LUFS).

    Uses EBU R128 loudness normalization for consistent perceived volume.

    Args:
        audio_bytes: MP3 audio data as bytes
        target_lufs: Target integrated loudness in LUFS (default: -16.0)
                    -16 LUFS is standard for streaming/games
                    -23 LUFS is broadcast standard

    Returns:
        Normalized audio as bytes
    """
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_in:
        tmp_in.write(audio_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path + ".norm.mp3"

    try:
        # loudnorm filter: I=target loudness, TP=true peak limit, LRA=loudness range
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", tmp_in_path,
                "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
                "-q:a", "2",
                tmp_out_path
            ],
            capture_output=True,
            check=True,
        )

        with open(tmp_out_path, "rb") as f:
            return f.read()

    finally:
        Path(tmp_in_path).unlink(missing_ok=True)
        Path(tmp_out_path).unlink(missing_ok=True)


def normalize_file(
    input_path: Path,
    output_path: Path | None = None,
    target_lufs: float = DEFAULT_TARGET_LUFS,
) -> Path:
    """
    Normalize loudness of an existing MP3 file.

    Args:
        input_path: Path to input MP3 file
        output_path: Path for output file (overwrites input if not specified)
        target_lufs: Target loudness in LUFS

    Returns:
        Path to the normalized file
    """
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path

    # Use temp file if overwriting input
    if output_path == input_path:
        tmp_out = input_path.with_suffix(".tmp.mp3")
    else:
        tmp_out = output_path

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            "-q:a", "2",
            str(tmp_out)
        ],
        capture_output=True,
        check=True,
    )

    if tmp_out != output_path:
        tmp_out.replace(output_path)

    return output_path


def normalize_directory(
    directory: Path,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    recursive: bool = True,
) -> list[Path]:
    """
    Normalize loudness of all MP3 files in a directory.

    Args:
        directory: Directory containing MP3 files
        target_lufs: Target loudness in LUFS
        recursive: If True, process subdirectories as well

    Returns:
        List of normalized file paths
    """
    directory = Path(directory)
    pattern = "**/*.mp3" if recursive else "*.mp3"

    processed = []
    for mp3_file in sorted(directory.glob(pattern)):
        print(f"[normalize] Processing: {mp3_file}")
        normalize_file(mp3_file, target_lufs=target_lufs)
        processed.append(mp3_file)

    return processed


def apply_fade_out(audio_bytes: bytes, duration_ms: int = DEFAULT_FADE_DURATION_MS) -> bytes:
    """
    Apply a fade-out effect to audio data using ffmpeg.

    Args:
        audio_bytes: MP3 audio data as bytes
        duration_ms: Fade duration in milliseconds (default: 100ms)

    Returns:
        Processed audio as bytes
    """
    duration_sec = duration_ms / 1000.0

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_in:
        tmp_in.write(audio_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path + ".out.mp3"

    try:
        # Get audio duration first
        probe_result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                tmp_in_path
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        total_duration = float(probe_result.stdout.strip())
        fade_start = max(0, total_duration - duration_sec)

        # Apply fade-out filter
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", tmp_in_path,
                "-af", f"afade=t=out:st={fade_start}:d={duration_sec}",
                "-q:a", "2",  # Good quality
                tmp_out_path
            ],
            capture_output=True,
            check=True,
        )

        with open(tmp_out_path, "rb") as f:
            return f.read()

    finally:
        Path(tmp_in_path).unlink(missing_ok=True)
        Path(tmp_out_path).unlink(missing_ok=True)


def process_file(
    input_path: Path,
    output_path: Path | None = None,
    duration_ms: int = DEFAULT_FADE_DURATION_MS,
) -> Path:
    """
    Apply fade-out to an existing MP3 file.

    Args:
        input_path: Path to input MP3 file
        output_path: Path for output file (overwrites input if not specified)
        duration_ms: Fade duration in milliseconds

    Returns:
        Path to the processed file
    """
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path
    duration_sec = duration_ms / 1000.0

    # Get audio duration
    probe_result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(input_path)
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    total_duration = float(probe_result.stdout.strip())
    fade_start = max(0, total_duration - duration_sec)

    # Use temp file if overwriting input
    if output_path == input_path:
        tmp_out = input_path.with_suffix(".tmp.mp3")
    else:
        tmp_out = output_path

    # Apply fade-out
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", f"afade=t=out:st={fade_start}:d={duration_sec}",
            "-q:a", "2",
            str(tmp_out)
        ],
        capture_output=True,
        check=True,
    )

    # Replace original if needed
    if tmp_out != output_path:
        tmp_out.replace(output_path)

    return output_path


def process_directory(
    directory: Path,
    duration_ms: int = DEFAULT_FADE_DURATION_MS,
    recursive: bool = True,
) -> list[Path]:
    """
    Apply fade-out to all MP3 files in a directory.

    Args:
        directory: Directory containing MP3 files
        duration_ms: Fade duration in milliseconds
        recursive: If True, process subdirectories as well

    Returns:
        List of processed file paths
    """
    directory = Path(directory)
    pattern = "**/*.mp3" if recursive else "*.mp3"

    processed = []
    for mp3_file in sorted(directory.glob(pattern)):
        print(f"[fade-out] Processing: {mp3_file}")
        process_file(mp3_file, duration_ms=duration_ms)
        processed.append(mp3_file)

    return processed


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Apply audio effects to MP3 files"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # fade-out command for single file
    fade_parser = subparsers.add_parser("fade-out", help="Apply fade-out to a file")
    fade_parser.add_argument("input", type=Path, help="Input MP3 file")
    fade_parser.add_argument("-o", "--output", type=Path, help="Output file (overwrites input if not specified)")
    fade_parser.add_argument("-d", "--duration", type=int, default=DEFAULT_FADE_DURATION_MS,
                            help=f"Fade duration in ms (default: {DEFAULT_FADE_DURATION_MS})")

    # batch command for directory (fade-out)
    batch_parser = subparsers.add_parser("batch", help="Apply fade-out to all MP3s in a directory")
    batch_parser.add_argument("directory", type=Path, help="Directory to process")
    batch_parser.add_argument("-d", "--duration", type=int, default=DEFAULT_FADE_DURATION_MS,
                             help=f"Fade duration in ms (default: {DEFAULT_FADE_DURATION_MS})")
    batch_parser.add_argument("--no-recursive", action="store_true", help="Don't process subdirectories")

    # normalize command for single file
    norm_parser = subparsers.add_parser("normalize", help="Normalize loudness of a file")
    norm_parser.add_argument("input", type=Path, help="Input MP3 file")
    norm_parser.add_argument("-o", "--output", type=Path, help="Output file (overwrites input if not specified)")
    norm_parser.add_argument("-l", "--lufs", type=float, default=DEFAULT_TARGET_LUFS,
                            help=f"Target loudness in LUFS (default: {DEFAULT_TARGET_LUFS})")

    # normalize-batch command for directory
    norm_batch_parser = subparsers.add_parser("normalize-batch", help="Normalize loudness of all MP3s in a directory")
    norm_batch_parser.add_argument("directory", type=Path, help="Directory to process")
    norm_batch_parser.add_argument("-l", "--lufs", type=float, default=DEFAULT_TARGET_LUFS,
                                   help=f"Target loudness in LUFS (default: {DEFAULT_TARGET_LUFS})")
    norm_batch_parser.add_argument("--no-recursive", action="store_true", help="Don't process subdirectories")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "fade-out":
        output = process_file(args.input, args.output, args.duration)
        print(f"Processed: {output}")

    elif args.command == "batch":
        processed = process_directory(
            args.directory,
            duration_ms=args.duration,
            recursive=not args.no_recursive,
        )
        print(f"\nProcessed {len(processed)} files")

    elif args.command == "normalize":
        output = normalize_file(args.input, args.output, args.lufs)
        print(f"Normalized: {output}")

    elif args.command == "normalize-batch":
        processed = normalize_directory(
            args.directory,
            target_lufs=args.lufs,
            recursive=not args.no_recursive,
        )
        print(f"\nNormalized {len(processed)} files")


if __name__ == "__main__":
    main()

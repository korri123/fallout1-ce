#!/usr/bin/env python3
"""
Compare enhanced_dialogue_cache.json with npc_dialogue.json (source of truth).
Detects potential mismatches where the LLM may have paired the wrong lines.

Usage:
    python3 compare_dialogue.py           # Just report issues
    python3 compare_dialogue.py --purge   # Remove WARNING+ entries and audio files
"""

import argparse
import json
import os
import re
from difflib import SequenceMatcher
from collections import defaultdict

# Paths
SOURCE_PATH = "../tools/npc_dialogue.json"
ENHANCED_PATH = "enhanced_dialogue_cache.json"
AUDIO_DIR = "extraspeech"

def load_source_of_truth(path):
    """Load npc_dialogue.json and build a lookup dict: 'npc:id' -> original_text"""
    with open(path, 'r') as f:
        data = json.load(f)

    lookup = {}
    for npc_name, npc_data in data.get("dialogue", {}).items():
        for line in npc_data.get("npc_lines", []):
            key = f"{npc_name}:{line['id']}"
            lookup[key] = line['text']

    return lookup

def load_enhanced_cache(path):
    """Load enhanced_dialogue_cache.json"""
    with open(path, 'r') as f:
        data = json.load(f)
    return data.get("entries", {})

def strip_stage_directions(text):
    """Remove [bracketed] and (parenthetical) stage directions from text"""
    # Remove all [...] patterns
    cleaned = re.sub(r'\[.*?\]', '', text)
    # Remove all (...) patterns (parenthetical stage directions)
    cleaned = re.sub(r'\(.*?\)', '', cleaned)
    # Clean up extra whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def get_words(text):
    """Extract words from text (lowercase, alphanumeric only)"""
    return set(re.findall(r'\b\w+\b', text.lower()))

def similarity_ratio(a, b):
    """Calculate similarity ratio using SequenceMatcher"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def word_overlap_ratio(original, enhanced):
    """
    Calculate bidirectional word overlap ratio.
    Returns the minimum of:
    - Forward: what % of original words appear in enhanced
    - Reverse: what % of enhanced words came from original

    This catches both:
    - Missing content (low forward ratio)
    - Added content (low reverse ratio)
    """
    orig_words = get_words(original)
    enh_words = get_words(enhanced)

    if not orig_words:
        return 1.0 if not enh_words else 0.0
    if not enh_words:
        return 0.0

    overlap = len(orig_words & enh_words)
    forward = overlap / len(orig_words)  # Are original words preserved?
    reverse = overlap / len(enh_words)   # Did enhanced add extra content?

    return min(forward, reverse)

def key_phrase_match(original, enhanced):
    """Check if key phrases from original appear in enhanced"""
    # Get multi-word phrases (2-3 words)
    orig_words = original.lower().split()
    enh_lower = enhanced.lower()

    if len(orig_words) < 2:
        return 1.0 if original.lower() in enh_lower else 0.0

    # Check bigrams
    bigrams = [' '.join(orig_words[i:i+2]) for i in range(len(orig_words)-1)]
    matches = sum(1 for bg in bigrams if bg in enh_lower)

    if not bigrams:
        return 1.0
    return matches / len(bigrams)

def calculate_score(original_raw, enhanced_raw):
    """
    Calculate a match score between original and enhanced text.
    Returns tuple: (score, details_dict)
    Score ranges from 0.0 (no match) to 1.0 (perfect match)

    Both original and enhanced have stage directions stripped before comparison,
    since the LLM replaces original [stage directions] with new [audio tags].
    """
    # Strip stage directions from BOTH - we only care about the spoken dialogue
    original = strip_stage_directions(original_raw)
    enhanced = strip_stage_directions(enhanced_raw)

    # Handle case where original was ONLY stage directions (no spoken text)
    if not original:
        # If original had no spoken text, enhanced should also have none
        # (or just audio tags which strip to empty)
        if not enhanced:
            return 1.0, {
                'sequence_similarity': 1.0,
                'word_overlap': 1.0,
                'phrase_match': 1.0,
                'length_ratio': 1.0,
                'exact_match': True,
                'original_stripped': original,
                'enhanced_stripped': enhanced,
                'stage_direction_only': True
            }
        # Original was only stage directions but enhanced has spoken text - suspicious
        return 0.0, {
            'sequence_similarity': 0.0,
            'word_overlap': 0.0,
            'phrase_match': 0.0,
            'length_ratio': 0.0,
            'exact_match': False,
            'original_stripped': original,
            'enhanced_stripped': enhanced,
            'stage_direction_only': True
        }

    # Metrics
    seq_ratio = similarity_ratio(original, enhanced)
    word_ratio = word_overlap_ratio(original, enhanced)
    phrase_ratio = key_phrase_match(original, enhanced)

    # Length difference penalty
    len_orig = len(original)
    len_enh = len(enhanced)
    len_ratio = min(len_orig, len_enh) / max(len_orig, len_enh) if max(len_orig, len_enh) > 0 else 1.0

    # Check for exact match (after stripping)
    exact_match = original.strip().lower() == enhanced.strip().lower()

    # Weighted score
    if exact_match:
        score = 1.0
    else:
        score = (
            seq_ratio * 0.35 +
            word_ratio * 0.35 +
            phrase_ratio * 0.20 +
            len_ratio * 0.10
        )

    details = {
        'sequence_similarity': seq_ratio,
        'word_overlap': word_ratio,
        'phrase_match': phrase_ratio,
        'length_ratio': len_ratio,
        'exact_match': exact_match,
        'original_stripped': original,
        'enhanced_stripped': enhanced,
        'stage_direction_only': False
    }

    return score, details

def classify_error(score):
    """Classify the severity of potential error"""
    if score >= 0.95:
        return "OK"
    elif score >= 0.80:
        return "MINOR"
    elif score >= 0.60:
        return "WARNING"
    elif score >= 0.40:
        return "ERROR"
    else:
        return "CRITICAL"

def key_to_audio_path(key):
    """Convert 'npc:id' to 'extraspeech/npc/id.mp3'"""
    parts = key.split(':')
    if len(parts) != 2:
        return None
    npc, line_id = parts
    return os.path.join(AUDIO_DIR, npc, f"{line_id}.mp3")

def purge_bad_entries(keys_to_remove, enhanced_data):
    """
    Remove bad entries from enhanced_dialogue_cache.json and delete audio files.
    Returns counts of removed items.
    """
    removed_json = 0
    removed_audio = 0
    audio_not_found = 0

    # Remove from JSON
    for key in keys_to_remove:
        if key in enhanced_data:
            del enhanced_data[key]
            removed_json += 1

    # Save updated JSON
    with open(ENHANCED_PATH, 'w') as f:
        json.dump({"version": 1, "entries": enhanced_data}, f, indent=2)

    # Remove audio files
    for key in keys_to_remove:
        audio_path = key_to_audio_path(key)
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
            removed_audio += 1
        else:
            audio_not_found += 1

    return removed_json, removed_audio, audio_not_found

def main():
    parser = argparse.ArgumentParser(description='Compare and optionally purge dialogue entries')
    parser.add_argument('--purge', action='store_true',
                        help='Remove WARNING+ entries from JSON and delete their audio files')
    args = parser.parse_args()

    print("Loading source of truth...")
    source = load_source_of_truth(SOURCE_PATH)
    print(f"  Loaded {len(source)} lines from npc_dialogue.json")

    print("Loading enhanced cache...")
    enhanced = load_enhanced_cache(ENHANCED_PATH)
    print(f"  Loaded {len(enhanced)} entries from enhanced_dialogue_cache.json")

    print("\n" + "="*80)
    print("COMPARISON RESULTS")
    print("="*80 + "\n")

    issues = defaultdict(list)
    missing_in_source = []
    all_scores = []

    for key, enhanced_text in sorted(enhanced.items()):
        if key == "version":
            continue

        if key not in source:
            missing_in_source.append(key)
            continue

        original_text = source[key]
        score, details = calculate_score(original_text, enhanced_text)
        all_scores.append(score)

        severity = classify_error(score)
        if severity != "OK":
            issues[severity].append({
                'key': key,
                'score': score,
                'original': original_text,
                'enhanced': enhanced_text,
                'details': details
            })

    # Report missing keys
    if missing_in_source:
        print(f"KEYS IN ENHANCED BUT NOT IN SOURCE ({len(missing_in_source)}):")
        for key in missing_in_source[:10]:
            print(f"  - {key}")
        if len(missing_in_source) > 10:
            print(f"  ... and {len(missing_in_source) - 10} more")
        print()

    # Report issues by severity
    for severity in ["CRITICAL", "ERROR", "WARNING", "MINOR"]:
        if issues[severity]:
            print(f"\n{'='*80}")
            print(f"{severity} ({len(issues[severity])} issues)")
            print("="*80)

            for issue in sorted(issues[severity], key=lambda x: x['score']):
                print(f"\n[{issue['key']}] Score: {issue['score']:.3f}")
                print(f"  ORIGINAL:  {issue['original']}")
                print(f"  ENHANCED:  {issue['enhanced']}")
                print(f"  STRIPPED:  {issue['details']['enhanced_stripped']}")
                print(f"  Seq: {issue['details']['sequence_similarity']:.3f} | "
                      f"Words: {issue['details']['word_overlap']:.3f} | "
                      f"Phrases: {issue['details']['phrase_match']:.3f} | "
                      f"LenRatio: {issue['details']['length_ratio']:.3f}")

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total enhanced entries: {len(enhanced) - 1}")  # -1 for version
    print(f"Missing in source: {len(missing_in_source)}")
    print(f"  CRITICAL (score < 0.40): {len(issues['CRITICAL'])}")
    print(f"  ERROR (0.40-0.60):       {len(issues['ERROR'])}")
    print(f"  WARNING (0.60-0.80):     {len(issues['WARNING'])}")
    print(f"  MINOR (0.80-0.95):       {len(issues['MINOR'])}")
    print(f"  OK (score >= 0.95):      {len(all_scores) - sum(len(v) for v in issues.values())}")

    if all_scores:
        print(f"\nAverage score: {sum(all_scores) / len(all_scores):.3f}")
        print(f"Minimum score: {min(all_scores):.3f}")

    # Purge if requested
    if args.purge:
        # Collect keys to remove: WARNING, ERROR, CRITICAL + missing in source
        keys_to_remove = set(missing_in_source)
        for severity in ["CRITICAL", "ERROR", "WARNING"]:
            for issue in issues[severity]:
                keys_to_remove.add(issue['key'])

        if keys_to_remove:
            print("\n" + "="*80)
            print("PURGING BAD ENTRIES")
            print("="*80)
            print(f"Removing {len(keys_to_remove)} entries...")

            removed_json, removed_audio, audio_not_found = purge_bad_entries(keys_to_remove, enhanced)

            print(f"  Removed from JSON: {removed_json}")
            print(f"  Audio files deleted: {removed_audio}")
            print(f"  Audio files not found: {audio_not_found}")

            # Log NPCs that need regeneration
            npcs_to_regen = defaultdict(list)
            for key in keys_to_remove:
                parts = key.split(':')
                if len(parts) == 2:
                    npc, line_id = parts
                    npcs_to_regen[npc].append(line_id)

            print("\n" + "="*80)
            print("NPCs NEEDING REGENERATION")
            print("="*80)
            for npc in sorted(npcs_to_regen.keys()):
                line_ids = sorted(npcs_to_regen[npc], key=lambda x: int(x) if x.isdigit() else 0)
                print(f"  {npc}: {', '.join(line_ids)}")

            print(f"\nTotal: {len(npcs_to_regen)} NPCs with {len(keys_to_remove)} lines to regenerate")
            print("Done!")
        else:
            print("\nNo entries to purge.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Fallout 1 NPC Dialogue Extractor

Extracts all dialogue text from Fallout 1 message files (.msg) and groups
them by NPC/script name. Can read from loose files or DAT archives.

Usage:
    python extract_dialogue.py <game_data_path> [--output dialogue.json]

    game_data_path: Path to Fallout 1 data directory containing:
        - scripts/scripts.lst (or SCRIPTS/SCRIPTS.LST)
        - text/<language>/dialog/*.msg files
        OR
        - master.dat file

Examples:
    python extract_dialogue.py /path/to/fallout1/data --output npc_dialogue.json
    python extract_dialogue.py /path/to/fallout1 --language english
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fallout_data import DATArchive, MsgParser, ScriptsListParser, MessageEntry


@dataclass
class DialogueEntry:
    """A single dialogue entry from a .msg file"""
    message_id: int
    audio_file: str
    text: str


@dataclass
class NPCDialogue:
    """All dialogue for a single NPC/script"""
    script_name: str
    script_index: int
    dialogue_file: str
    npc_name: str = ""  # Human-readable NPC name if known
    entries: List[DialogueEntry] = field(default_factory=list)

    def to_dict(self):
        result = {
            'script_name': self.script_name,
            'script_index': self.script_index,
            'dialogue_file': self.dialogue_file,
            'entries': [
                {
                    'id': e.message_id,
                    'audio': e.audio_file,
                    'text': e.text
                }
                for e in self.entries
            ]
        }
        if self.npc_name:
            result['npc_name'] = self.npc_name
        return result


# Known script-to-NPC name mappings for Fallout 1
KNOWN_NPC_NAMES = {
    # Vault 13
    'gencaved': 'Generic Cave Dweller',
    'v13elder': 'Vault 13 Overseer',
    # Shady Sands
    'aradesh': 'Aradesh',
    'tandi': 'Tandi',
    'seth': 'Seth',
    'ian': 'Ian',
    'katrina': 'Katrina',
    'razlo': 'Razlo',
    # Junktown
    'killian': 'Killian Darkwater',
    'gizmo': 'Gizmo',
    'tycho': 'Tycho',
    'neal': 'Neal',
    'saul': 'Saul',
    'lars': 'Lars',
    'marcelle': 'Marcelle',
    'doc_morbid': 'Doc Morbid',
    'ismarc': 'Ismarc',
    'skulz': 'Skulz Gang',
    'skul2': 'Skulz Gang',
    # Hub
    'decker': 'Decker',
    'hightowe': 'Hightower',
    'loxley': 'Loxley',
    'demetre': 'Demetre',
    'harold': 'Harold',
    'butch': 'Butch',
    'lorenzo': 'Lorenzo',
    'daren': 'Daren Hightower',
    'beth': 'Beth',
    'irwin': 'Irwin',
    'jake': 'Jake',
    'justin': 'Justin',
    'cal': 'Cal',
    # Brotherhood of Steel
    'paladin': 'BoS Paladin',
    'talus': 'Talus',
    'cabbot': 'Cabbot',
    'mathia': 'General Maxson',
    'rhombus': 'Rhombus',
    'vree': 'Vree',
    'sophi': 'Sophia',
    # Boneyard
    'adytmayr': 'Adytum Mayor',
    'razor': 'Razor',
    'blades': 'Blades Gang',
    'regulator': 'Regulator',
    'jon': 'Jon Zimmerman',
    'chuck': 'Chuck',
    'nicole': 'Nicole',
    'talius': 'Talius',
    'followers': 'Followers of the Apocalypse',
    # Necropolis
    'set': 'Set',
    'ghoul': 'Ghoul',
    'harry': 'Harry',
    # Cathedral/Children of the Cathedral
    'morpheus': 'Morpheus',
    'lasher': 'Lasher',
    'visquis': 'Visquis',
    'jain': 'Jain',
    'disciple': 'Disciple',
    'nightkin': 'Nightkin',
    # Military Base
    'masterat': 'Master (Attack)',
    'master': 'The Master',
    'supermu': 'Super Mutant',
    'mutant': 'Mutant',
    'lieuten': 'The Lieutenant',
    # Raiders
    'garl': 'Garl Death-Hand',
    'raider': 'Raider',
    'petrox': 'Petrox',
    # Glow
    'zax': 'ZAX',
    # Generic/Companions
    'dogmeat': 'Dogmeat'
}


def _convert_message_entry(entry: MessageEntry) -> DialogueEntry:
    """Convert MessageEntry to DialogueEntry."""
    return DialogueEntry(
        message_id=entry.message_id,
        audio_file=entry.audio_file,
        text=entry.text
    )


class DialogueExtractor:
    """Main dialogue extractor class"""

    def __init__(self, game_path: str, language: str = 'english'):
        self.game_path = Path(game_path)
        self.language = language
        self.npc_names: Dict[int, str] = {}

    def extract(self) -> Dict[str, NPCDialogue]:
        """Extract all NPC dialogue from the game data"""
        if self._has_loose_files():
            return self._extract_from_loose_files()
        elif self._has_dat_archive():
            return self._extract_from_dat()
        else:
            raise FileNotFoundError(
                f"Could not find game data at {self.game_path}. "
                "Expected either loose files or master.dat"
            )

    def _has_loose_files(self) -> bool:
        """Check if loose files exist"""
        scripts_lst = self._find_file(['scripts/scripts.lst', 'SCRIPTS/SCRIPTS.LST'])
        return scripts_lst is not None

    def _has_dat_archive(self) -> bool:
        """Check if master.dat exists"""
        dat_paths = [
            self.game_path / 'master.dat',
            self.game_path / 'MASTER.DAT',
        ]
        return any(p.exists() for p in dat_paths)

    def _find_file(self, possible_paths: List[str]) -> Optional[Path]:
        """Find a file trying multiple possible paths"""
        for rel_path in possible_paths:
            full_path = self.game_path / rel_path
            if full_path.exists():
                return full_path
        return None

    def _lookup_npc_name(self, script_name: str) -> str:
        """Look up human-readable NPC name from script name"""
        name_lower = script_name.lower()
        if name_lower in KNOWN_NPC_NAMES:
            return KNOWN_NPC_NAMES[name_lower]

        for known_script, npc_name in KNOWN_NPC_NAMES.items():
            if name_lower.startswith(known_script):
                return npc_name
            if known_script.startswith(name_lower):
                return npc_name

        return ""

    def _extract_from_loose_files(self) -> Dict[str, NPCDialogue]:
        """Extract dialogue from loose files"""
        result = {}

        scripts_lst_path = self._find_file([
            'scripts/scripts.lst',
            'SCRIPTS/SCRIPTS.LST',
            'data/scripts/scripts.lst',
            'DATA/SCRIPTS/SCRIPTS.LST'
        ])

        if not scripts_lst_path:
            raise FileNotFoundError("Could not find scripts.lst")

        print(f"Reading scripts list from: {scripts_lst_path}")
        scripts = ScriptsListParser.parse(scripts_lst_path.read_bytes())
        print(f"Found {len(scripts)} scripts")

        dialog_dirs = [
            f'text/{self.language}/dialog',
            f'TEXT/{self.language.upper()}/DIALOG',
            f'data/text/{self.language}/dialog',
            f'DATA/TEXT/{self.language.upper()}/DIALOG'
        ]

        dialog_dir = None
        for d in dialog_dirs:
            full_path = self.game_path / d
            if full_path.exists():
                dialog_dir = full_path
                break

        if not dialog_dir:
            raise FileNotFoundError(
                f"Could not find dialogue directory for language '{self.language}'"
            )

        print(f"Reading dialogue from: {dialog_dir}")

        for script_index, script_name in scripts:
            msg_file = dialog_dir / f'{script_name}.msg'
            if not msg_file.exists():
                msg_file = dialog_dir / f'{script_name.upper()}.MSG'

            if msg_file.exists():
                msg_entries = MsgParser.parse(msg_file.read_bytes())
                if msg_entries:
                    npc_name = self._lookup_npc_name(script_name)
                    entries = [_convert_message_entry(e) for e in msg_entries]

                    npc = NPCDialogue(
                        script_name=script_name,
                        script_index=script_index,
                        dialogue_file=str(msg_file.relative_to(self.game_path)),
                        npc_name=npc_name,
                        entries=entries
                    )
                    result[script_name] = npc

        print(f"Extracted dialogue from {len(result)} scripts")
        return result

    def _extract_from_dat(self) -> Dict[str, NPCDialogue]:
        """Extract dialogue from DAT archive"""
        result = {}

        dat_path = self.game_path / 'master.dat'
        if not dat_path.exists():
            dat_path = self.game_path / 'MASTER.DAT'

        print(f"Opening DAT archive: {dat_path}")

        with DATArchive(str(dat_path)) as dat:
            all_files = dat.list_files()
            dialog_prefix = f'TEXT\\{self.language.upper()}\\DIALOG\\'
            dialog_files = [f for f in all_files
                          if f.startswith(dialog_prefix) and f.endswith('.MSG')]

            print(f"Found {len(dialog_files)} dialogue files")

            script_indices = {}
            scripts_data = dat.read_file('scripts\\scripts.lst')
            if scripts_data:
                scripts = ScriptsListParser.parse(scripts_data)
                script_indices = {name: idx for idx, name in scripts}

            for msg_path in dialog_files:
                filename = msg_path.split('\\')[-1]
                script_name = filename.replace('.MSG', '').lower()

                msg_data = dat.read_file(msg_path)
                if msg_data:
                    msg_entries = MsgParser.parse(msg_data)
                    if msg_entries:
                        npc_name = self._lookup_npc_name(script_name)
                        script_index = script_indices.get(script_name, -1)
                        entries = [_convert_message_entry(e) for e in msg_entries]

                        npc = NPCDialogue(
                            script_name=script_name,
                            script_index=script_index,
                            dialogue_file=msg_path.lower(),
                            npc_name=npc_name,
                            entries=entries
                        )
                        result[script_name] = npc

        print(f"Extracted dialogue from {len(result)} scripts")
        return result


def filter_npc_dialogue(dialogue: Dict[str, NPCDialogue],
                         exclude_player: bool = True) -> Dict[str, NPCDialogue]:
    """
    Filter dialogue to focus on NPC lines.

    In Fallout 1, message files often contain both NPC dialogue and player
    response options. This function attempts to filter based on common patterns.
    """
    if not exclude_player:
        return dialogue

    result = {}
    for script_name, npc in dialogue.items():
        filtered_entries = []
        for entry in npc.entries:
            if entry.audio_file:
                filtered_entries.append(entry)
                continue

            if entry.message_id >= 200:
                filtered_entries.append(entry)
            elif entry.message_id < 100:
                filtered_entries.append(entry)

        if filtered_entries:
            result[script_name] = NPCDialogue(
                script_name=npc.script_name,
                script_index=npc.script_index,
                dialogue_file=npc.dialogue_file,
                entries=filtered_entries
            )

    return result


def export_to_json(dialogue: Dict[str, NPCDialogue], output_path: str):
    """Export dialogue to JSON file"""
    data = {
        'metadata': {
            'total_scripts': len(dialogue),
            'total_entries': sum(len(d.entries) for d in dialogue.values())
        },
        'dialogue': {
            name: d.to_dict() for name, d in sorted(dialogue.items())
        }
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Exported to: {output_path}")


def export_to_text(dialogue: Dict[str, NPCDialogue], output_path: str):
    """Export dialogue to readable text file"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("FALLOUT 1 NPC DIALOGUE EXTRACTION\n")
        f.write("=" * 80 + "\n\n")

        for script_name in sorted(dialogue.keys()):
            npc = dialogue[script_name]
            f.write("-" * 80 + "\n")
            if npc.npc_name:
                f.write(f"NPC: {npc.npc_name}\n")
            f.write(f"Script: {script_name}\n")
            f.write(f"Script Index: {npc.script_index}\n")
            f.write(f"Source File: {npc.dialogue_file}\n")
            f.write(f"Total Lines: {len(npc.entries)}\n")
            f.write("-" * 80 + "\n\n")

            for entry in sorted(npc.entries, key=lambda e: e.message_id):
                f.write(f"[{entry.message_id}]")
                if entry.audio_file:
                    f.write(f" ({entry.audio_file})")
                f.write("\n")
                f.write(f"{entry.text}\n\n")

            f.write("\n")

    print(f"Exported to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract NPC dialogue from Fallout 1 game data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/fallout1/data
  %(prog)s /path/to/fallout1 --language english --output dialogue.json
  %(prog)s /path/to/fallout1 --format text --output dialogue.txt
  %(prog)s /path/to/fallout1 --include-player-responses
        """
    )

    parser.add_argument(
        'game_path',
        help='Path to Fallout 1 data directory (containing scripts/ and text/ or master.dat)'
    )

    parser.add_argument(
        '--language', '-l',
        default='english',
        help='Language for dialogue files (default: english)'
    )

    parser.add_argument(
        '--output', '-o',
        default='fallout1_dialogue.json',
        help='Output file path (default: fallout1_dialogue.json)'
    )

    parser.add_argument(
        '--format', '-f',
        choices=['json', 'text'],
        default='json',
        help='Output format (default: json)'
    )

    parser.add_argument(
        '--include-player-responses',
        action='store_true',
        help='Include player dialogue responses (by default, attempts to filter to NPC-only)'
    )

    parser.add_argument(
        '--list-scripts',
        action='store_true',
        help='Just list available scripts without extracting dialogue'
    )

    args = parser.parse_args()

    try:
        extractor = DialogueExtractor(args.game_path, args.language)

        if args.list_scripts:
            if extractor._has_loose_files():
                scripts_lst = extractor._find_file([
                    'scripts/scripts.lst',
                    'SCRIPTS/SCRIPTS.LST'
                ])
                if scripts_lst:
                    scripts = ScriptsListParser.parse(scripts_lst.read_bytes())
                    print(f"Found {len(scripts)} scripts:")
                    for idx, name in scripts:
                        print(f"  {idx:3d}: {name}")
            return 0

        dialogue = extractor.extract()

        if not args.include_player_responses:
            dialogue = filter_npc_dialogue(dialogue)

        output_path = args.output
        if args.format == 'json' and not output_path.endswith('.json'):
            output_path += '.json'
        elif args.format == 'text' and not output_path.endswith('.txt'):
            output_path += '.txt'

        if args.format == 'json':
            export_to_json(dialogue, output_path)
        else:
            export_to_text(dialogue, output_path)

        print(f"\nSummary:")
        print(f"  Total scripts with dialogue: {len(dialogue)}")
        print(f"  Total dialogue entries: {sum(len(d.entries) for d in dialogue.values())}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

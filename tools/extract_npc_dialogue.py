#!/usr/bin/env python3
"""
Fallout 1 NPC Dialogue Extractor (Bytecode-based)

Extracts NPC dialogue from Fallout 1 scripts by analyzing bytecode for
dialogue function calls (gsay_reply, gsay_option, etc.) and looking up
the corresponding messages in .MSG files.

This approach extracts ONLY dialogue that is actually used in the game,
distinguishing:
- NPC replies (what NPCs say): gsay_reply, gsay_message
- Player options (what player can choose): gsay_option, giq_option

Usage:
    python extract_npc_dialogue.py /Applications/Fallout --output dialogue.txt

The messageListId in dialogue calls equals script_index + 1, where
script_index comes from scripts.lst.
"""

import argparse
import json
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

# Add the tools directory to the path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fallout_data import (
    DATArchive, MsgParser, ScriptsListParser, MessageEntry,
    Opcode
)


# Dialogue opcodes from script.py
DIALOGUE_OPCODES = {
    Opcode.GSAY_REPLY: ('gsay_reply', 2, 'npc'),      # gsay_reply(messageListId, msg)
    Opcode.GSAY_MESSAGE: ('gsay_message', 3, 'npc'),  # gsay_message(messageListId, msg, reaction)
    Opcode.GSAY_OPTION: ('gsay_option', 4, 'player'), # gsay_option(messageListId, msg, proc, reaction)
    Opcode.GIQ_OPTION: ('giq_option', 5, 'player'),   # giq_option(iq, messageListId, msg, proc, reaction)
}

# Known NPC names for common scripts
KNOWN_NPC_NAMES = {
    'aradesh': 'Aradesh',
    'tandi': 'Tandi',
    'seth': 'Seth',
    'ian': 'Ian',
    'katrina': 'Katrina',
    'razlo': 'Razlo',
    'killian': 'Killian Darkwater',
    'gizmo': 'Gizmo',
    'tycho': 'Tycho',
    'neal': 'Neal',
    'saul': 'Saul',
    'lars': 'Lars',
    'marcelle': 'Marcelle',
    'doc_morbid': 'Doc Morbid',
    'ismarc': 'Ismarc',
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
    'paladin': 'BoS Paladin',
    'talus': 'Talus',
    'cabbot': 'Cabbot',
    'mathia': 'General Maxson',
    'rhombus': 'Rhombus',
    'vree': 'Vree',
    'sophi': 'Sophia',
    'razor': 'Razor',
    'nicole': 'Nicole',
    'talius': 'Talius',
    'set': 'Set',
    'harry': 'Harry',
    'morpheus': 'Morpheus',
    'lasher': 'Lasher',
    'jain': 'Jain',
    'master': 'The Master',
    'lieuten': 'The Lieutenant',
    'garl': 'Garl Death-Hand',
    'zax': 'ZAX',
    'dogmeat': 'Dogmeat',
    'v13elder': 'Vault 13 Overseer',
}


@dataclass
class DialogueCall:
    """A dialogue function call found in bytecode."""
    script_file: str        # Source .INT file
    offset: int             # Bytecode offset
    opcode_name: str        # Function name (gsay_reply, etc.)
    message_list_id: int    # Script index + 1
    message_id: int         # Message ID within .MSG file
    call_type: str          # 'npc' or 'player'


@dataclass
class DialogueLine:
    """A resolved dialogue line with text."""
    message_id: int
    text: str
    audio_file: str = ""
    call_type: str = ""
    source_scripts: List[str] = field(default_factory=list)


@dataclass
class NPCDialogue:
    """All dialogue for an NPC."""
    script_name: str
    script_index: int
    npc_name: str = ""
    npc_lines: List[DialogueLine] = field(default_factory=list)
    player_options: List[DialogueLine] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'script_name': self.script_name,
            'script_index': self.script_index,
            'npc_name': self.npc_name,
            'npc_line_count': len(self.npc_lines),
            'player_option_count': len(self.player_options),
            'npc_lines': [
                {
                    'id': line.message_id,
                    'text': line.text,
                    'audio': line.audio_file,
                }
                for line in sorted(self.npc_lines, key=lambda x: x.message_id)
            ],
            'player_options': [
                {
                    'id': line.message_id,
                    'text': line.text,
                }
                for line in sorted(self.player_options, key=lambda x: x.message_id)
            ],
        }


class DialogueExtractor:
    """Extract dialogue from Fallout 1 script bytecode."""

    def __init__(self, game_path: str, language: str = 'english'):
        self.game_path = Path(game_path)
        self.language = language
        self.dat: Optional[DATArchive] = None
        self._script_list: Dict[int, str] = {}  # script_index -> script_name
        self._msg_cache: Dict[str, Dict[int, MessageEntry]] = {}

    def extract(self, include_player_options: bool = False) -> Dict[str, NPCDialogue]:
        """
        Extract all NPC dialogue from scripts.

        Args:
            include_player_options: If True, also include player dialogue options.

        Returns:
            Dict mapping script_name -> NPCDialogue
        """
        dat_path = self._find_dat_file()
        if not dat_path:
            raise FileNotFoundError(f"Could not find MASTER.DAT in {self.game_path}")

        result: Dict[str, NPCDialogue] = {}
        calls_by_target: Dict[int, List[DialogueCall]] = defaultdict(list)

        with DATArchive(str(dat_path)) as self.dat:
            self._load_script_list()

            # Find all script files
            all_files = self.dat.list_files()
            int_files = [f for f in all_files
                        if f.endswith('.INT') and 'SCRIPTS' in f.upper()]

            print(f"Found {len(int_files)} script files")
            print(f"Loaded {len(self._script_list)} script name mappings")

            # Parse each script and find dialogue calls
            for script_path in sorted(int_files):
                calls = self._find_dialogue_calls_in_script(script_path)
                for call in calls:
                    calls_by_target[call.message_list_id].append(call)

            print(f"Found dialogue calls targeting {len(calls_by_target)} different message lists")

            # Resolve calls to actual dialogue text
            for msg_list_id, calls in sorted(calls_by_target.items()):
                script_index = msg_list_id - 1
                script_name = self._script_list.get(script_index, f"unknown_{script_index}")

                msg_dict = self._load_messages(script_name)
                if not msg_dict:
                    continue

                npc_name = self._lookup_npc_name(script_name)

                # Collect unique message IDs by type
                npc_msg_ids: Set[int] = set()
                player_msg_ids: Set[int] = set()

                for call in calls:
                    if call.call_type == 'npc':
                        npc_msg_ids.add(call.message_id)
                    else:
                        player_msg_ids.add(call.message_id)

                # Build NPC lines
                npc_lines = []
                for msg_id in sorted(npc_msg_ids):
                    if msg_id in msg_dict:
                        entry = msg_dict[msg_id]
                        npc_lines.append(DialogueLine(
                            message_id=msg_id,
                            text=entry.text,
                            audio_file=entry.audio_file,
                            call_type='npc',
                        ))

                # Build player options
                player_options = []
                if include_player_options:
                    for msg_id in sorted(player_msg_ids):
                        if msg_id in msg_dict:
                            entry = msg_dict[msg_id]
                            player_options.append(DialogueLine(
                                message_id=msg_id,
                                text=entry.text,
                                audio_file=entry.audio_file,
                                call_type='player',
                            ))

                if npc_lines or player_options:
                    result[script_name] = NPCDialogue(
                        script_name=script_name,
                        script_index=script_index,
                        npc_name=npc_name,
                        npc_lines=npc_lines,
                        player_options=player_options,
                    )

        return result

    def _find_dat_file(self) -> Optional[Path]:
        """Find MASTER.DAT file."""
        candidates = [
            self.game_path / 'MASTER.DAT',
            self.game_path / 'master.dat',
            self.game_path / 'Master.dat',
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    def _load_script_list(self):
        """Load scripts.lst to map indices to script names."""
        paths_to_try = ['SCRIPTS\\SCRIPTS.LST', 'scripts/scripts.lst']
        for path in paths_to_try:
            data = self.dat.read_file(path)
            if data:
                scripts = ScriptsListParser.parse(data)
                for idx, name in scripts:
                    self._script_list[idx] = name
                return

    def _load_messages(self, script_name: str) -> Dict[int, MessageEntry]:
        """Load message file for a script."""
        if script_name in self._msg_cache:
            return self._msg_cache[script_name]

        paths_to_try = [
            f"TEXT\\{self.language.upper()}\\DIALOG\\{script_name.upper()}.MSG",
            f"text/{self.language}/dialog/{script_name}.msg",
        ]

        for path in paths_to_try:
            data = self.dat.read_file(path)
            if data:
                entries = MsgParser.parse(data)
                msg_dict = {e.message_id: e for e in entries}
                self._msg_cache[script_name] = msg_dict
                return msg_dict

        self._msg_cache[script_name] = {}
        return {}

    def _lookup_npc_name(self, script_name: str) -> str:
        """Look up human-readable NPC name."""
        name_lower = script_name.lower()
        if name_lower in KNOWN_NPC_NAMES:
            return KNOWN_NPC_NAMES[name_lower]
        # Try partial match
        for key, name in KNOWN_NPC_NAMES.items():
            if name_lower.startswith(key) or key.startswith(name_lower):
                return name
        return ""

    def _find_dialogue_calls_in_script(self, script_path: str) -> List[DialogueCall]:
        """Find all dialogue calls in a script file."""
        data = self.dat.read_file(script_path)
        if not data:
            return []

        calls = []

        # Scan bytecode for dialogue opcodes
        # We look backwards from each dialogue opcode to find the PUSH instructions
        offset = 0
        while offset + 2 <= len(data):
            try:
                opcode = struct.unpack('>H', data[offset:offset+2])[0]
            except:
                offset += 2
                continue

            if opcode in DIALOGUE_OPCODES:
                name, arg_count, call_type = DIALOGUE_OPCODES[opcode]
                call = self._try_extract_dialogue_call(
                    data, offset, name, arg_count, call_type, script_path
                )
                if call:
                    calls.append(call)

            offset += 2

        return calls

    def _try_extract_dialogue_call(
        self, data: bytes, opcode_offset: int, name: str, call_type: str, script_path: str
    ) -> Optional[DialogueCall]:
        """
        Try to extract dialogue call arguments by looking backwards.

        The bytecode pushes arguments in order, so for gsay_reply(listId, msgId):
        - offset-12: PUSH INT listId (6 bytes: 2 opcode + 4 value)
        - offset-6:  PUSH INT msgId  (6 bytes)
        - offset-0:  GSAY_REPLY      (2 bytes)
        """
        PUSH_INT = 0xC001
        PUSH_SIZE = 6  # 2 byte opcode + 4 byte value

        # Calculate where to look for arguments based on function signature
        if name == 'gsay_reply':
            # gsay_reply(messageListId, msg) - 2 args
            msg_offset = opcode_offset - PUSH_SIZE
            list_offset = opcode_offset - 2 * PUSH_SIZE
        elif name == 'gsay_message':
            # gsay_message(messageListId, msg, reaction) - 3 args
            # reaction is at -6, msg at -12, listId at -18
            msg_offset = opcode_offset - 2 * PUSH_SIZE
            list_offset = opcode_offset - 3 * PUSH_SIZE
        elif name == 'gsay_option':
            # gsay_option(messageListId, msg, proc, reaction) - 4 args
            # reaction at -6, proc at -12, msg at -18, listId at -24
            msg_offset = opcode_offset - 3 * PUSH_SIZE
            list_offset = opcode_offset - 4 * PUSH_SIZE
        elif name == 'giq_option':
            # giq_option(iq, messageListId, msg, proc, reaction) - 5 args
            # reaction at -6, proc at -12, msg at -18, listId at -24, iq at -30
            msg_offset = opcode_offset - 3 * PUSH_SIZE
            list_offset = opcode_offset - 4 * PUSH_SIZE
        else:
            return None

        # Validate offsets
        if list_offset < 0 or msg_offset < 0:
            return None
        if list_offset + PUSH_SIZE > len(data) or msg_offset + PUSH_SIZE > len(data):
            return None

        # Check that we have PUSH INT instructions at the expected locations
        try:
            list_opcode = struct.unpack('>H', data[list_offset:list_offset+2])[0]
            msg_opcode = struct.unpack('>H', data[msg_offset:msg_offset+2])[0]
        except:
            return None

        if list_opcode != PUSH_INT or msg_opcode != PUSH_INT:
            return None

        # Extract values
        try:
            message_list_id = struct.unpack('>i', data[list_offset+2:list_offset+6])[0]
            message_id = struct.unpack('>i', data[msg_offset+2:msg_offset+6])[0]
        except:
            return None

        # Validate values - must be reasonable
        if message_list_id <= 0 or message_list_id > 1000:
            return None
        if message_id < 0 or message_id > 100000:
            return None

        return DialogueCall(
            script_file=script_path,
            offset=opcode_offset,
            opcode_name=name,
            message_list_id=message_list_id,
            message_id=message_id,
            call_type=call_type,
        )


def export_to_json(dialogue: Dict[str, NPCDialogue], output_path: str):
    """Export dialogue to JSON file."""
    total_npc_lines = sum(len(d.npc_lines) for d in dialogue.values())
    total_player_options = sum(len(d.player_options) for d in dialogue.values())

    data = {
        'metadata': {
            'description': 'NPC dialogue extracted from Fallout 1 script bytecode',
            'total_npcs': len(dialogue),
            'total_npc_lines': total_npc_lines,
            'total_player_options': total_player_options,
        },
        'dialogue': {
            name: d.to_dict() for name, d in sorted(dialogue.items())
        }
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Exported to: {output_path}")


def export_to_text(dialogue: Dict[str, NPCDialogue], output_path: str,
                   include_player_options: bool = False):
    """Export dialogue to readable text file, grouped by NPC."""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("FALLOUT 1 NPC DIALOGUE\n")
        f.write("Extracted from script bytecode via gsay_reply/gsay_message analysis\n")
        f.write("=" * 80 + "\n\n")

        total_lines = 0
        for script_name in sorted(dialogue.keys()):
            npc = dialogue[script_name]

            if not npc.npc_lines and not npc.player_options:
                continue

            f.write("-" * 80 + "\n")
            if npc.npc_name:
                f.write(f"NPC: {npc.npc_name}\n")
            f.write(f"Script: {script_name}\n")
            f.write(f"Script Index: {npc.script_index}\n")
            f.write(f"NPC Lines: {len(npc.npc_lines)}\n")
            if include_player_options:
                f.write(f"Player Options: {len(npc.player_options)}\n")
            f.write("-" * 80 + "\n\n")

            # Write NPC lines
            if npc.npc_lines:
                f.write("=== NPC DIALOGUE ===\n\n")
                for line in sorted(npc.npc_lines, key=lambda x: x.message_id):
                    f.write(f"[{line.message_id}]")
                    if line.audio_file:
                        f.write(f" ({line.audio_file})")
                    f.write("\n")
                    f.write(f"{line.text}\n\n")
                    total_lines += 1

            # Write player options
            if include_player_options and npc.player_options:
                f.write("=== PLAYER OPTIONS ===\n\n")
                for line in sorted(npc.player_options, key=lambda x: x.message_id):
                    f.write(f"[{line.message_id}] {line.text}\n\n")

            f.write("\n")

    print(f"Exported {total_lines} NPC dialogue lines to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract NPC dialogue from Fallout 1 script bytecode',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /Applications/Fallout
  %(prog)s /path/to/fallout1 --output npc_dialogue.json
  %(prog)s /path/to/fallout1 --format text --output npc_dialogue.txt
  %(prog)s /path/to/fallout1 --include-player-options
        """
    )

    parser.add_argument(
        'game_path',
        help='Path to Fallout 1 directory containing MASTER.DAT'
    )

    parser.add_argument(
        '--language', '-l',
        default='english',
        help='Language for dialogue files (default: english)'
    )

    parser.add_argument(
        '--output', '-o',
        default='fallout1_npc_dialogue',
        help='Output file path (extension added based on format)'
    )

    parser.add_argument(
        '--format', '-f',
        choices=['json', 'text', 'both'],
        default='text',
        help='Output format (default: text)'
    )

    parser.add_argument(
        '--include-player-options',
        action='store_true',
        help='Include player dialogue options in addition to NPC replies'
    )

    args = parser.parse_args()

    try:
        extractor = DialogueExtractor(args.game_path, args.language)

        print("Extracting NPC dialogue from script bytecode...")
        dialogue = extractor.extract(include_player_options=args.include_player_options)

        if not dialogue:
            print("No dialogue found!")
            return 1

        # Determine output paths
        base_path = args.output
        if base_path.endswith('.json') or base_path.endswith('.txt'):
            base_path = base_path.rsplit('.', 1)[0]

        if args.format in ('json', 'both'):
            export_to_json(dialogue, base_path + '.json')

        if args.format in ('text', 'both'):
            export_to_text(dialogue, base_path + '.txt',
                          include_player_options=args.include_player_options)

        # Summary
        total_npc = sum(len(d.npc_lines) for d in dialogue.values())
        total_player = sum(len(d.player_options) for d in dialogue.values())

        print(f"\nSummary:")
        print(f"  NPCs with dialogue: {len(dialogue)}")
        print(f"  Total NPC dialogue lines: {total_npc}")
        if args.include_player_options:
            print(f"  Total player options: {total_player}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())

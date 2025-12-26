#!/usr/bin/env python3
"""
Fallout 1 NPC Dialogue Extractor (Bytecode-based)

Extracts NPC dialogue from Fallout 1, distinguishing NPC speech from player responses.

Fallout 1's dialogue system is largely data-driven rather than script-driven.
Unlike Fallout 2, most dialogue is stored in .MSG files with the flow controlled
by the engine based on message ID ranges, not explicit script bytecode calls.

This tool extracts NPC dialogue using heuristics:
1. NPC dialogue entries typically have audio file references (e.g., "Ara_1g")
2. Player responses typically have no audio file
3. "Look at" descriptions (usually ID 100) are filtered out

Usage:
    python extract_npc_dialogue.py <game_path> [--output dialogue.json]

    game_path: Path to Fallout 1 directory containing MASTER.DAT
"""

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from fallout_data import DATArchive, MsgParser, ScriptsListParser, MessageEntry


# Opcodes relevant to dialogue
OPCODE_PUSH_INT = 0xC001      # Push integer constant
OPCODE_PUSH_STRING = 0x9001   # Push static string
OPCODE_GSAY_REPLY = 0x811E    # gsay_reply(messageListId, msg)
OPCODE_GSAY_MESSAGE = 0x8122  # gsay_message(messageListId, msg, reaction)
OPCODE_GSAY_OPTION = 0x811F   # gsay_option(messageListId, msg, proc, reaction)
OPCODE_GIQ_OPTION = 0x8123    # giq_option(iq_level, messageListId, msg, proc, reaction)


@dataclass
class DialogueCall:
    """Represents a dialogue function call found in bytecode"""
    opcode: int
    script_offset: int
    message_list_id: int
    message_id: int
    opcode_name: str = ""

    def __post_init__(self):
        opcode_names = {
            OPCODE_GSAY_REPLY: "gsay_reply",
            OPCODE_GSAY_MESSAGE: "gsay_message",
            OPCODE_GSAY_OPTION: "gsay_option",
            OPCODE_GIQ_OPTION: "giq_option",
        }
        self.opcode_name = opcode_names.get(self.opcode, f"opcode_{self.opcode:04X}")


@dataclass
class NPCDialogueEntry:
    """A single dialogue entry with its source information"""
    message_id: int
    text: str
    audio_file: str = ""
    call_type: str = ""  # reply, message, option
    script_offset: int = 0


@dataclass
class NPCDialogueData:
    """All dialogue for a single NPC/script"""
    script_name: str
    script_index: int
    script_file: str
    dialogue_file: str
    entries: List[NPCDialogueEntry] = field(default_factory=list)

    def to_dict(self):
        return {
            'script_name': self.script_name,
            'script_index': self.script_index,
            'script_file': self.script_file,
            'dialogue_file': self.dialogue_file,
            'entry_count': len(self.entries),
            'entries': [
                {
                    'id': e.message_id,
                    'text': e.text,
                    'audio': e.audio_file,
                    'type': e.call_type,
                }
                for e in sorted(self.entries, key=lambda x: x.message_id)
            ]
        }


class ScriptBytecodeParser:
    """Parser for Fallout 1 script bytecode (.int files)"""

    def __init__(self, data: bytes):
        self.data = data
        self.size = len(data)

    def read_int16_be(self, offset: int) -> int:
        """Read big-endian 16-bit unsigned integer"""
        if offset + 2 > self.size:
            return 0
        return struct.unpack('>H', self.data[offset:offset+2])[0]

    def read_int32_be(self, offset: int) -> int:
        """Read big-endian 32-bit unsigned integer"""
        if offset + 4 > self.size:
            return 0
        return struct.unpack('>I', self.data[offset:offset+4])[0]

    def read_int32_be_signed(self, offset: int) -> int:
        """Read big-endian 32-bit signed integer"""
        if offset + 4 > self.size:
            return 0
        return struct.unpack('>i', self.data[offset:offset+4])[0]

    def get_header_info(self) -> Dict:
        """Parse script header to find code section"""
        proc_offset = 42
        if proc_offset + 4 > self.size:
            return {'code_start': 42, 'proc_count': 0}

        proc_count = self.read_int32_be(proc_offset)
        PROCEDURE_SIZE = 24

        identifiers_offset = proc_offset + 4 + proc_count * PROCEDURE_SIZE
        if identifiers_offset + 4 > self.size:
            return {'code_start': 42, 'proc_count': proc_count}

        identifiers_size = self.read_int32_be(identifiers_offset)

        strings_offset = identifiers_offset + identifiers_size + 4
        if strings_offset + 4 > self.size:
            return {'code_start': identifiers_offset, 'proc_count': proc_count}

        strings_size = self.read_int32_be(strings_offset)

        if strings_size == 0xFFFFFFFF:
            code_start = strings_offset
        else:
            code_start = strings_offset + strings_size + 4

        return {
            'code_start': code_start,
            'proc_count': proc_count,
            'identifiers_offset': identifiers_offset,
            'strings_offset': strings_offset,
        }

    def find_dialogue_calls(self) -> List[DialogueCall]:
        """Find all dialogue-related function calls in the bytecode."""
        calls = []
        header = self.get_header_info()
        scan_start = min(header.get('code_start', 42), 42)

        i = scan_start
        while i < self.size - 2:
            opcode = self.read_int16_be(i)

            if opcode == OPCODE_GSAY_REPLY:
                call = self._try_parse_two_arg_call(i, OPCODE_GSAY_REPLY)
                if call:
                    calls.append(call)
            elif opcode == OPCODE_GSAY_MESSAGE:
                call = self._try_parse_three_arg_call(i, OPCODE_GSAY_MESSAGE)
                if call:
                    calls.append(call)
            elif opcode == OPCODE_GSAY_OPTION:
                call = self._try_parse_four_arg_call(i, OPCODE_GSAY_OPTION)
                if call:
                    calls.append(call)
            elif opcode == OPCODE_GIQ_OPTION:
                call = self._try_parse_five_arg_call(i, OPCODE_GIQ_OPTION)
                if call:
                    calls.append(call)

            i += 2

        return calls

    def _try_parse_two_arg_call(self, opcode_offset: int, opcode: int) -> Optional[DialogueCall]:
        """Parse two-argument call pattern."""
        if opcode_offset < 12:
            return None

        push1_offset = opcode_offset - 12
        push2_offset = opcode_offset - 6

        push1_op = self.read_int16_be(push1_offset)
        push2_op = self.read_int16_be(push2_offset)

        if push1_op == OPCODE_PUSH_INT and push2_op == OPCODE_PUSH_INT:
            message_list_id = self.read_int32_be_signed(push1_offset + 2)
            message_id = self.read_int32_be_signed(push2_offset + 2)

            if 0 < message_list_id < 1000 and 0 <= message_id < 100000:
                return DialogueCall(
                    opcode=opcode,
                    script_offset=opcode_offset,
                    message_list_id=message_list_id,
                    message_id=message_id,
                )

        return None

    def _try_parse_three_arg_call(self, opcode_offset: int, opcode: int) -> Optional[DialogueCall]:
        """Parse three-argument call."""
        if opcode_offset < 18:
            return None

        push1_offset = opcode_offset - 18
        push2_offset = opcode_offset - 12
        push3_offset = opcode_offset - 6

        push1_op = self.read_int16_be(push1_offset)
        push2_op = self.read_int16_be(push2_offset)
        push3_op = self.read_int16_be(push3_offset)

        if push1_op == OPCODE_PUSH_INT and push2_op == OPCODE_PUSH_INT and push3_op == OPCODE_PUSH_INT:
            message_list_id = self.read_int32_be_signed(push1_offset + 2)
            message_id = self.read_int32_be_signed(push2_offset + 2)

            if 0 < message_list_id < 1000 and 0 <= message_id < 100000:
                return DialogueCall(
                    opcode=opcode,
                    script_offset=opcode_offset,
                    message_list_id=message_list_id,
                    message_id=message_id,
                )

        return None

    def _try_parse_four_arg_call(self, opcode_offset: int, opcode: int) -> Optional[DialogueCall]:
        """Parse four-argument call."""
        if opcode_offset < 24:
            return None

        push1_offset = opcode_offset - 24
        push2_offset = opcode_offset - 18

        push1_op = self.read_int16_be(push1_offset)
        push2_op = self.read_int16_be(push2_offset)

        if push1_op == OPCODE_PUSH_INT and push2_op == OPCODE_PUSH_INT:
            message_list_id = self.read_int32_be_signed(push1_offset + 2)
            message_id = self.read_int32_be_signed(push2_offset + 2)

            if 0 < message_list_id < 1000 and 0 <= message_id < 100000:
                return DialogueCall(
                    opcode=opcode,
                    script_offset=opcode_offset,
                    message_list_id=message_list_id,
                    message_id=message_id,
                )

        return None

    def _try_parse_five_arg_call(self, opcode_offset: int, opcode: int) -> Optional[DialogueCall]:
        """Parse five-argument call."""
        if opcode_offset < 30:
            return None

        push2_offset = opcode_offset - 24
        push3_offset = opcode_offset - 18

        push2_op = self.read_int16_be(push2_offset)
        push3_op = self.read_int16_be(push3_offset)

        if push2_op == OPCODE_PUSH_INT and push3_op == OPCODE_PUSH_INT:
            message_list_id = self.read_int32_be_signed(push2_offset + 2)
            message_id = self.read_int32_be_signed(push3_offset + 2)

            if 0 < message_list_id < 1000 and 0 <= message_id < 100000:
                return DialogueCall(
                    opcode=opcode,
                    script_offset=opcode_offset,
                    message_list_id=message_list_id,
                    message_id=message_id,
                )

        return None


class NPCDialogueExtractor:
    """Extract NPC dialogue by analyzing script bytecode."""

    def __init__(self, game_path: str, language: str = 'english'):
        self.game_path = Path(game_path)
        self.language = language
        self.dat: Optional[DATArchive] = None
        self._msg_cache: Dict[str, List[MessageEntry]] = {}
        self._script_list: Dict[int, str] = {}

    def extract(self, reply_only: bool = True) -> Dict[str, NPCDialogueData]:
        """
        Extract NPC dialogue from all scripts.

        Args:
            reply_only: If True, only extract gsay_reply calls (NPC speech).
        """
        dat_path = self.game_path / 'MASTER.DAT'
        if not dat_path.exists():
            dat_path = self.game_path / 'master.dat'

        if not dat_path.exists():
            raise FileNotFoundError(f"Could not find MASTER.DAT in {self.game_path}")

        result: Dict[str, NPCDialogueData] = {}

        with DATArchive(str(dat_path)) as self.dat:
            self._load_script_list()

            all_files = self.dat.list_files()
            int_files = [f for f in all_files if f.endswith('.INT') and f.startswith('SCRIPTS\\')]

            print(f"Found {len(int_files)} script files to analyze...")

            for script_path in sorted(int_files):
                script_data = self.dat.read_file(script_path)
                if not script_data:
                    continue

                filename = script_path.split('\\')[-1]
                script_name = filename.replace('.INT', '').lower()

                parser = ScriptBytecodeParser(script_data)
                calls = parser.find_dialogue_calls()

                if not calls:
                    continue

                if reply_only:
                    calls = [c for c in calls if c.opcode == OPCODE_GSAY_REPLY]

                if not calls:
                    continue

                calls_by_list: Dict[int, List[DialogueCall]] = {}
                for call in calls:
                    if call.message_list_id not in calls_by_list:
                        calls_by_list[call.message_list_id] = []
                    calls_by_list[call.message_list_id].append(call)

                for msg_list_id, list_calls in calls_by_list.items():
                    msg_script_name = self._script_list.get(msg_list_id - 1, script_name)
                    msg_entries = self._load_dialogue(msg_script_name)
                    if not msg_entries:
                        continue

                    msg_by_id: Dict[int, MessageEntry] = {}
                    for entry in msg_entries:
                        msg_by_id[entry.message_id] = entry

                    unique_msg_ids: Set[int] = set()
                    for call in list_calls:
                        unique_msg_ids.add(call.message_id)

                    entries: List[NPCDialogueEntry] = []
                    for msg_id in sorted(unique_msg_ids):
                        if msg_id in msg_by_id:
                            entry = msg_by_id[msg_id]
                            call_type = "reply"
                            for c in list_calls:
                                if c.message_id == msg_id:
                                    if c.opcode == OPCODE_GSAY_MESSAGE:
                                        call_type = "message"
                                    elif c.opcode in (OPCODE_GSAY_OPTION, OPCODE_GIQ_OPTION):
                                        call_type = "option"
                                    break

                            entries.append(NPCDialogueEntry(
                                message_id=msg_id,
                                text=entry.text,
                                audio_file=entry.audio_file,
                                call_type=call_type,
                            ))

                    if entries:
                        key = msg_script_name
                        if key not in result:
                            result[key] = NPCDialogueData(
                                script_name=msg_script_name,
                                script_index=msg_list_id - 1,
                                script_file=script_path.lower(),
                                dialogue_file=f"text\\{self.language}\\dialog\\{msg_script_name}.msg",
                                entries=entries,
                            )
                        else:
                            existing_ids = {e.message_id for e in result[key].entries}
                            for e in entries:
                                if e.message_id not in existing_ids:
                                    result[key].entries.append(e)

        return result

    def _load_script_list(self):
        """Load scripts.lst to map indices to script names"""
        data = self.dat.read_file('SCRIPTS/SCRIPTS.LST')
        if data:
            scripts = ScriptsListParser.parse(data)
            for idx, name in scripts:
                self._script_list[idx] = name

    def _load_dialogue(self, script_name: str) -> List[MessageEntry]:
        """Load dialogue entries for a script"""
        if script_name in self._msg_cache:
            return self._msg_cache[script_name]

        paths = [
            f"TEXT/{self.language.upper()}/DIALOG/{script_name.upper()}.MSG",
            f"text/{self.language}/dialog/{script_name}.msg",
        ]

        for path in paths:
            data = self.dat.read_file(path)
            if data:
                entries = MsgParser.parse(data)
                self._msg_cache[script_name] = entries
                return entries

        self._msg_cache[script_name] = []
        return []


def export_to_json(dialogue: Dict[str, NPCDialogueData], output_path: str):
    """Export dialogue to JSON file"""
    total_entries = sum(len(d.entries) for d in dialogue.values())

    data = {
        'metadata': {
            'description': 'NPC dialogue extracted from Fallout 1 script bytecode',
            'method': 'Analyzed gsay_reply opcode calls in .int script files',
            'total_npcs': len(dialogue),
            'total_dialogue_lines': total_entries,
        },
        'dialogue': {
            name: d.to_dict() for name, d in sorted(dialogue.items())
        }
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Exported to: {output_path}")


def export_to_text(dialogue: Dict[str, NPCDialogueData], output_path: str):
    """Export dialogue to readable text file"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("FALLOUT 1 NPC DIALOGUE (EXTRACTED FROM SCRIPT BYTECODE)\n")
        f.write("=" * 80 + "\n\n")
        f.write("This file contains only dialogue lines that are actually used as NPC\n")
        f.write("replies in the game's dialogue system (via gsay_reply opcode).\n\n")

        for script_name in sorted(dialogue.keys()):
            npc = dialogue[script_name]
            f.write("-" * 80 + "\n")
            f.write(f"NPC Script: {script_name}\n")
            f.write(f"Script Index: {npc.script_index}\n")
            f.write(f"Script File: {npc.script_file}\n")
            f.write(f"Dialogue File: {npc.dialogue_file}\n")
            f.write(f"Reply Lines: {len(npc.entries)}\n")
            f.write("-" * 80 + "\n\n")

            for entry in sorted(npc.entries, key=lambda e: e.message_id):
                f.write(f"[{entry.message_id}]")
                if entry.audio_file:
                    f.write(f" ({entry.audio_file})")
                if entry.call_type != "reply":
                    f.write(f" [{entry.call_type}]")
                f.write("\n")
                f.write(f"{entry.text}\n\n")

            f.write("\n")

    print(f"Exported to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract NPC dialogue from Fallout 1 script bytecode',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/fallout1
  %(prog)s /path/to/fallout1 --output npc_dialogue.json
  %(prog)s /path/to/fallout1 --format text --output npc_dialogue.txt
  %(prog)s /path/to/fallout1 --include-options
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
        default='fallout1_npc_dialogue.json',
        help='Output file path (default: fallout1_npc_dialogue.json)'
    )

    parser.add_argument(
        '--format', '-f',
        choices=['json', 'text'],
        default='json',
        help='Output format (default: json)'
    )

    parser.add_argument(
        '--include-options',
        action='store_true',
        help='Include player dialogue options (gsay_option) in addition to NPC replies'
    )

    args = parser.parse_args()

    try:
        extractor = NPCDialogueExtractor(args.game_path, args.language)

        print("Extracting NPC dialogue from script bytecode...")
        dialogue = extractor.extract(reply_only=not args.include_options)

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
        print(f"  NPCs with dialogue: {len(dialogue)}")
        print(f"  Total dialogue lines: {sum(len(d.entries) for d in dialogue.values())}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())

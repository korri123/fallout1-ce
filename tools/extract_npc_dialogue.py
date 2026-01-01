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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

# Add the tools directory to the path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fallout_data import (
    DATArchive, MsgParser, ScriptsListParser, MessageEntry,
    Opcode, ProtoParser, MapParser
)
import re


# Dialogue opcodes from script.py
# Only gsay_reply/gsay_message with integer literal arguments are extracted
DIALOGUE_OPCODES = {
    Opcode.GSAY_REPLY: ('gsay_reply', 2, 'npc'),      # gsay_reply(messageListId, msg)
    Opcode.GSAY_MESSAGE: ('gsay_message', 3, 'npc'),  # gsay_message(messageListId, msg, reaction)
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


# Kill types from proto_types.h - indicates creature type for voice
KILL_TYPES = {
    0: 'Human (Male)',
    1: 'Human (Female)',
    2: 'Child',
    3: 'Super Mutant',
    4: 'Ghoul',
    5: 'Brahmin',
    6: 'Radscorpion',
    7: 'Rat',
    8: 'Floater',
    9: 'Centaur',
    10: 'Robot',
    11: 'Dog',
    12: 'Mantis',
    13: 'Deathclaw',
    14: 'Plant',
}


@dataclass
class NPCDialogue:
    """All dialogue for an NPC."""
    script_name: str
    script_index: int
    npc_name: str = ""
    gender: str = ""
    description: str = ""
    creature_type: str = ""  # From kill_type: Human, Super Mutant, Ghoul, Robot, etc.
    appearance: str = ""  # "You see..." description
    faction: str = ""  # AI personality type
    npc_lines: List[DialogueLine] = field(default_factory=list)
    player_options: List[DialogueLine] = field(default_factory=list)

    def to_dict(self) -> dict:
        # Get sample lines for voice characterization (first 3 unique lines)
        sample_lines = []
        for line in sorted(self.npc_lines, key=lambda x: x.message_id):
            if len(sample_lines) < 3 and line.text:
                sample_lines.append(line.text[:200])  # Truncate long lines

        return {
            'script_name': self.script_name,
            'script_index': self.script_index,
            'npc_name': self.npc_name,
            # Voice generation fields
            'voice_info': {
                'gender': self.gender,
                'creature_type': self.creature_type,
                'appearance': self.appearance,
                'faction': self.faction,
                'sample_lines': sample_lines,
            },
            'description': self.description,
            'npc_line_count': len(self.npc_lines),
            'player_option_count': len(self.player_options),
            'npc_lines': [
                {
                    'id': line.message_id,
                    'text': line.text,
                    **(({'audio': line.audio_file}) if line.audio_file else {}),
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
        # Proto data: name -> (proto, full_name, description, kill_type, age, ai_packet)
        self._name_to_proto: Dict[str, tuple] = {}
        # Proto data by PID: pid -> (proto, full_name, description, kill_type, ai_packet)
        self._pid_to_proto: Dict[int, tuple] = {}
        # AI packet names: packet_num -> name
        self._ai_packets: Dict[int, str] = {}
        # Script index -> list of critter PIDs from placed critters on maps
        self._script_to_critter_pids: Dict[int, List[int]] = {}

    def extract(self, include_player_options: bool = False) -> Dict[str, NPCDialogue]:
        """
        Extract all NPC dialogue from scripts.

        Args:
            include_player_options: If True, also include player dialogue options.

        Returns:
            Dict mapping script_name -> NPCDialogue

        Note: Due to bugs in original game data, some scripts (e.g., SAUL.INT)
        have embedded message_list_id values that don't match the current
        scripts.lst. We use the script filename to determine the MSG file
        rather than the embedded message_list_id.
        """
        dat_path = self._find_dat_file()
        if not dat_path:
            raise FileNotFoundError(f"Could not find MASTER.DAT in {self.game_path}")

        result: Dict[str, NPCDialogue] = {}

        with DATArchive(str(dat_path)) as self.dat:
            self._load_script_list()
            self._load_ai_packets()
            self._load_proto_data()
            self._build_script_to_critter_pid_map()

            # Build reverse mapping: script_name -> index
            name_to_index = {v: k for k, v in self._script_list.items()}

            # Find all script files
            all_files = self.dat.list_files()
            int_files = [f for f in all_files
                        if f.endswith('.INT') and 'SCRIPTS' in f.upper()]

            print(f"Found {len(int_files)} script files")
            print(f"Loaded {len(self._script_list)} script name mappings")

            scripts_with_dialogue = 0

            # Parse each script and find dialogue calls
            # Use embedded message_list_id to find MSG file (not script filename)
            for script_path in sorted(int_files):
                # Extract script name from path (e.g., SCRIPTS\SAUL.INT -> saul)
                filename = script_path.split('\\')[-1]
                script_name = filename.replace('.INT', '').lower()

                calls = self._find_dialogue_calls_in_script(script_path)
                if not calls:
                    continue

                # Load all MSG files referenced by dialogue calls
                # Scripts can reference multiple MSG files (e.g., ian, tycho, katja)
                # message_list_id = script_index + 1, so script_index = message_list_id - 1
                msg_dicts: Dict[int, Dict[int, MessageEntry]] = {}
                for call in calls:
                    list_id = call.message_list_id
                    if list_id not in msg_dicts:
                        msg_script_index = list_id - 1
                        msg_script_name = self._script_list.get(msg_script_index, script_name)
                        msg_dict = self._load_messages(msg_script_name)
                        if not msg_dict:
                            # Fallback to script filename
                            msg_dict = self._load_messages(script_name)
                        msg_dicts[list_id] = msg_dict if msg_dict else {}

                # Skip if no messages could be loaded
                if not any(msg_dicts.values()):
                    continue

                scripts_with_dialogue += 1

                npc_name = self._lookup_npc_name(script_name)
                script_index = name_to_index.get(script_name, -1)

                # Collect unique (message_list_id, message_id) pairs by type
                npc_calls: Set[tuple] = set()
                player_calls: Set[tuple] = set()

                for call in calls:
                    if call.call_type == 'npc':
                        npc_calls.add((call.message_list_id, call.message_id))
                    else:
                        player_calls.add((call.message_list_id, call.message_id))

                # Build NPC lines - look up each message in its specific MSG file
                npc_lines = []
                for list_id, msg_id in sorted(npc_calls, key=lambda x: x[1]):
                    msg_dict = msg_dicts.get(list_id, {})
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
                    for list_id, msg_id in sorted(player_calls, key=lambda x: x[1]):
                        msg_dict = msg_dicts.get(list_id, {})
                        if msg_id in msg_dict:
                            entry = msg_dict[msg_id]
                            player_options.append(DialogueLine(
                                message_id=msg_id,
                                text=entry.text,
                                audio_file=entry.audio_file,
                                call_type='player',
                            ))

                if npc_lines or player_options:
                    # Look up voice-relevant info from proto data
                    # Use the first non-empty msg_dict for proto lookup
                    primary_msg_dict = next(
                        (d for d in msg_dicts.values() if d), {}
                    )
                    proto_info = self._lookup_proto_info(script_name, script_index, primary_msg_dict)

                    result[script_name] = NPCDialogue(
                        script_name=script_name,
                        script_index=script_index,
                        npc_name=npc_name,
                        gender=proto_info['gender'],
                        description=proto_info['description'],
                        creature_type=proto_info['creature_type'],
                        appearance=proto_info['appearance'],
                        faction=proto_info['faction'],
                        npc_lines=npc_lines,
                        player_options=player_options,
                    )

            print(f"Found {scripts_with_dialogue} scripts with dialogue calls")

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

    def _load_ai_packets(self):
        """Load AI packet names from AI.TXT."""
        ai_content = self.dat.read_file('DATA\\AI.TXT')
        if not ai_content:
            return

        text = ai_content.decode('utf-8', errors='replace')
        current_name = None

        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                current_name = line[1:-1]
            elif line.startswith('packet_num=') and current_name:
                try:
                    packet_num = int(line.split('=')[1].split(';')[0].strip())
                    self._ai_packets[packet_num] = current_name
                except:
                    pass

    def _load_proto_data(self):
        """Load all critter prototypes and build name-to-proto mapping."""
        # Load ALL critter protos
        critters_lst = self.dat.read_file('PROTO\\CRITTERS\\CRITTERS.LST')
        if not critters_lst:
            return

        lines = critters_lst.decode('utf-8', errors='replace').strip().split('\n')

        # Extended proto data: msg_id -> (proto, kill_type, age, ai_packet)
        all_protos = {}
        for line in lines:
            pro_file = line.strip()
            if not pro_file:
                continue
            content = self.dat.read_file(f'PROTO\\CRITTERS\\{pro_file}')
            if not content or len(content) < 412:
                continue

            proto = ProtoParser.parse_critter(content)
            if proto:
                # Extract additional fields
                # AI packet is at offset 36 in header
                ai_packet = struct.unpack('>i', content[36:40])[0]
                all_protos[proto.message_id] = (proto, proto.kill_type, proto.body_type, ai_packet)

        # Load critter messages (name, description)
        critter_messages = ProtoParser.load_critter_messages(self.dat, self.language)

        # Build name -> proto mapping with extended data
        # Also build pid -> proto mapping for direct PID lookup
        for msg_id, (proto, kill_type, body_type, ai_packet) in all_protos.items():
            name, desc = critter_messages.get(msg_id, ('', ''))
            # Store by PID for direct lookup from placed critters
            self._pid_to_proto[proto.pid] = (proto, name, desc, kill_type, ai_packet)
            if name:
                name_lower = name.lower()
                # Store: (proto, full_name, description, kill_type, ai_packet)
                self._name_to_proto[name_lower] = (proto, name, desc, kill_type, ai_packet)

    def _build_script_to_critter_pid_map(self):
        """
        Parse all maps to build a mapping from script index to critter PIDs.

        This allows us to get accurate proto data for placed critters that use
        generic templates (e.g., "Man in Leather Armor") by looking up the
        actual PID of the placed critter rather than trying to match by name.
        """
        print("Scanning maps for placed critters...")

        # Load proto types for complete map parsing
        item_types, scenery_types = MapParser.load_proto_types(self.dat)
        parser = MapParser(proto_item_types=item_types, proto_scenery_types=scenery_types)

        # Get all map files
        map_files = MapParser.list_maps(self.dat)
        maps_parsed = 0
        critters_found = 0

        for map_path in map_files:
            try:
                map_data = parser.parse_from_dat(self.dat, map_path)

                # Find all critters and their scripts
                for critter in map_data.critters:
                    # Find the script for this critter
                    script = map_data.get_script_for_object(critter)
                    if script and script.scr_script_idx >= 0:
                        script_idx = script.scr_script_idx
                        if script_idx not in self._script_to_critter_pids:
                            self._script_to_critter_pids[script_idx] = []
                        # Store the critter's PID
                        if critter.pid not in self._script_to_critter_pids[script_idx]:
                            self._script_to_critter_pids[script_idx].append(critter.pid)
                            critters_found += 1

                maps_parsed += 1
            except Exception:
                continue  # Skip problematic maps

        print(f"Parsed {maps_parsed} maps, found {critters_found} script-to-critter mappings")

    def _extract_name_from_yousee(self, text: str) -> str:
        """Extract NPC name from 'You see X.' message."""
        if not text:
            return ''
        match = re.match(r"You see (?:a |an )?(.+?)[.]?$", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ''

    def _find_best_proto_match(self, extracted_name: str, script_name: str,
                               min_name_len: int = 4) -> Optional[tuple]:
        """Find best matching proto for extracted name or script name."""
        if not extracted_name:
            extracted_lower = ''
        else:
            extracted_lower = extracted_name.lower()

        # First: exact match with extracted name
        if extracted_lower and extracted_lower in self._name_to_proto:
            return self._name_to_proto[extracted_lower]

        # Second: script name exact match
        if script_name and script_name in self._name_to_proto:
            return self._name_to_proto[script_name]

        # Third: extracted name starts with proto name (handles "Gizmo, the casino owner")
        # Or proto name starts with extracted name (handles "Killian" -> "Killian Darkwater")
        candidates = []
        for proto_name in self._name_to_proto:
            if len(proto_name) >= min_name_len:
                # Check if extracted starts with proto name
                if extracted_lower and (
                    extracted_lower.startswith(proto_name + ' ') or
                    extracted_lower.startswith(proto_name + ',') or
                    extracted_lower == proto_name
                ):
                    candidates.append((len(proto_name), proto_name, 'extracted_starts'))
                # Check if proto name starts with extracted (with word boundary)
                elif len(extracted_lower) >= min_name_len and (
                    proto_name.startswith(extracted_lower + ' ') or
                    proto_name == extracted_lower
                ):
                    candidates.append((len(proto_name), proto_name, 'proto_starts'))

        if candidates:
            # Prefer exact matches, then longest
            candidates.sort(key=lambda x: (x[2] == 'extracted_starts', x[0]), reverse=True)
            return self._name_to_proto[candidates[0][1]]

        # Fourth: proto name starts with script_name
        if script_name:
            for proto_name in self._name_to_proto:
                if proto_name.startswith(script_name + ' ') or proto_name == script_name:
                    return self._name_to_proto[proto_name]

        return None

    def _lookup_proto_info(self, script_name: str, script_index: int,
                           msg_dict: Dict[int, MessageEntry]) -> dict:
        """
        Look up voice-relevant info from proto data.

        First checks for placed critters using this script (from map parsing),
        then falls back to name-based matching for NPCs using generic templates.

        Args:
            script_name: Name of the script (e.g., 'saul')
            script_index: Index of the script in scripts.lst
            msg_dict: Message dictionary for this script's MSG file

        Returns:
            Dict with gender, description, creature_type, appearance, faction
        """
        result = {
            'gender': '',
            'description': '',
            'creature_type': '',
            'appearance': '',
            'faction': '',
        }

        # Get "You see..." text from message 100 for appearance
        yousee_entry = msg_dict.get(100)
        if yousee_entry:
            result['appearance'] = yousee_entry.text

        match = None

        # First: Try to find proto via placed critter PID from map data
        # This is the most accurate method for NPCs using generic templates
        if script_index >= 0 and script_index in self._script_to_critter_pids:
            critter_pids = self._script_to_critter_pids[script_index]
            if critter_pids:
                # Use the first placed critter's PID
                pid = critter_pids[0]
                if pid in self._pid_to_proto:
                    match = self._pid_to_proto[pid]

        # Fallback: Try name-based matching
        if not match:
            extracted_name = self._extract_name_from_yousee(
                yousee_entry.text if yousee_entry else ''
            )
            match = self._find_best_proto_match(extracted_name, script_name)

        if match:
            proto, full_name, description, kill_type, ai_packet = match
            result['gender'] = proto.gender_str
            result['description'] = description

            # For creature_type, use kill_type but correct for humans
            # The original data has some inconsistencies where female characters
            # have kill_type=0 (Human Male) despite being female
            if kill_type in (0, 1):  # Human Male or Human Female kill types
                # Use the actual gender from proto for humans
                result['creature_type'] = f'Human ({proto.gender_str})'
            else:
                result['creature_type'] = KILL_TYPES.get(kill_type, '')

            # Get AI personality name
            if ai_packet in self._ai_packets:
                result['faction'] = self._ai_packets[ai_packet]

        return result

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
                name, _arg_count, call_type = DIALOGUE_OPCODES[opcode]
                call = self._try_extract_dialogue_call(
                    data, offset, name, call_type, script_path
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

        # gsay_reply(messageListId, msg) - 2 args
        # gsay_message(messageListId, msg, reaction) - 3 args
        # Arguments must be integer literals (PUSH INT)
        if name == 'gsay_reply':
            # gsay_reply: stack is [messageListId, msg, OPCODE]
            msg_offset = opcode_offset - PUSH_SIZE
            list_offset = opcode_offset - 2 * PUSH_SIZE
        elif name == 'gsay_message':
            # gsay_message: stack is [messageListId, msg, reaction, OPCODE]
            # We skip reaction and extract messageListId and msg
            msg_offset = opcode_offset - 2 * PUSH_SIZE
            list_offset = opcode_offset - 3 * PUSH_SIZE
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


def filter_unvoiced(dialogue: Dict[str, NPCDialogue]) -> Dict[str, NPCDialogue]:
    """Filter dialogue to only include lines without existing audio files."""
    filtered = {}
    for script_name, npc in dialogue.items():
        # Filter NPC lines - keep only those without audio
        unvoiced_npc_lines = [line for line in npc.npc_lines if not line.audio_file]
        # Filter player options - keep only those without audio
        unvoiced_player_options = [line for line in npc.player_options if not line.audio_file]

        # Only include NPC if it has any unvoiced lines
        if unvoiced_npc_lines or unvoiced_player_options:
            filtered[script_name] = NPCDialogue(
                script_name=npc.script_name,
                script_index=npc.script_index,
                npc_name=npc.npc_name,
                gender=npc.gender,
                description=npc.description,
                creature_type=npc.creature_type,
                appearance=npc.appearance,
                faction=npc.faction,
                npc_lines=unvoiced_npc_lines,
                player_options=unvoiced_player_options,
            )
    return filtered


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

            # Voice-relevant info
            if npc.gender or npc.creature_type:
                f.write(f"Voice Info:\n")
                if npc.gender:
                    f.write(f"  Gender: {npc.gender}\n")
                if npc.creature_type:
                    f.write(f"  Creature Type: {npc.creature_type}\n")
                if npc.faction:
                    f.write(f"  Speaking Style: {npc.faction}\n")
                if npc.appearance:
                    f.write(f"  Appearance: {npc.appearance}\n")

            if npc.description:
                f.write(f"Description: {npc.description}\n")
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
  %(prog)s /path/to/fallout1 --unvoiced-only  # Only lines needing voice generation
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

    parser.add_argument(
        '--unvoiced-only',
        action='store_true',
        help='Only include dialogue lines without existing audio files'
    )

    args = parser.parse_args()

    try:
        extractor = DialogueExtractor(args.game_path, args.language)

        print("Extracting NPC dialogue from script bytecode...")
        dialogue = extractor.extract(include_player_options=args.include_player_options)

        if not dialogue:
            print("No dialogue found!")
            return 1

        # Apply unvoiced filter if requested
        if args.unvoiced_only:
            original_count = len(dialogue)
            original_lines = sum(len(d.npc_lines) for d in dialogue.values())
            dialogue = filter_unvoiced(dialogue)
            filtered_lines = sum(len(d.npc_lines) for d in dialogue.values())
            print(f"Filtered to unvoiced only: {len(dialogue)}/{original_count} NPCs, "
                  f"{filtered_lines}/{original_lines} lines")

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

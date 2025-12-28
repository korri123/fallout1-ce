"""
File format parsers for Fallout 1 data files.

Includes parsers for:
- .MSG files (dialogue/message text)
- scripts.lst (script index)
- .PRO files (prototype definitions)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .dat import DATArchive

__all__ = [
    'MessageEntry', 'MsgParser',
    'ScriptsListParser',
    'CritterProto', 'ProtoParser',
]


# Gender constants matching proto_types.h
GENDER_MALE = 0
GENDER_FEMALE = 1

# STAT_GENDER index in baseStats array (from stat_defs.h)
STAT_GENDER = 34

# Object types for PID
OBJ_TYPE_ITEM = 0
OBJ_TYPE_CRITTER = 1
OBJ_TYPE_SCENERY = 2
OBJ_TYPE_WALL = 3
OBJ_TYPE_TILE = 4
OBJ_TYPE_MISC = 5


@dataclass
class CritterProto:
    """Parsed critter prototype data."""
    pid: int  # Prototype ID
    message_id: int  # Base message ID for name/description lookup
    fid: int  # Frame ID for sprite
    sid: int  # Script ID (-1 if none)
    script_index: int  # Script index (extracted from SID)
    gender: int  # 0=Male, 1=Female
    head_fid: int  # Talking head FID
    ai_packet: int
    team: int
    body_type: int
    experience: int
    kill_type: int

    @property
    def gender_str(self) -> str:
        """Return gender as human-readable string."""
        if self.gender == GENDER_MALE:
            return "Male"
        elif self.gender == GENDER_FEMALE:
            return "Female"
        return "Unknown"

    @property
    def has_script(self) -> bool:
        """Return True if this critter has an attached script."""
        return self.sid >= 0 and self.script_index >= 0


@dataclass
class MessageEntry:
    """A single entry from a .MSG file."""
    message_id: int
    audio_file: str
    text: str

    def __repr__(self) -> str:
        audio_part = f", audio={self.audio_file!r}" if self.audio_file else ""
        text_preview = self.text[:50] + "..." if len(self.text) > 50 else self.text
        return f"MessageEntry(id={self.message_id}{audio_part}, text={text_preview!r})"


class MsgParser:
    """
    Parser for Fallout .MSG dialogue/message files.

    MSG files contain localized text strings with format:
        {number}{audio_filename}{text}

    Each field is delimited by braces. Newlines within fields are stripped.
    Encoding is Windows-1252 (CP1252).
    """

    @staticmethod
    def parse(content: bytes) -> List[MessageEntry]:
        """
        Parse a .MSG file.

        Args:
            content: Raw file bytes

        Returns:
            List of MessageEntry objects
        """
        entries = []
        text = content.decode('cp1252', errors='replace')

        pos = 0
        while pos < len(text):
            # Find start of number field
            start = text.find('{', pos)
            if start == -1:
                break

            # Parse three fields: {number}{audio}{text}
            num_str, pos = MsgParser._read_field(text, start)
            if num_str is None:
                break

            audio, pos = MsgParser._read_field(text, pos)
            if audio is None:
                break

            msg_text, pos = MsgParser._read_field(text, pos)
            if msg_text is None:
                break

            # Parse message ID
            try:
                num_str = num_str.strip()
                if num_str.lstrip('-+').isdigit():
                    msg_id = int(num_str)
                    entries.append(MessageEntry(
                        message_id=msg_id,
                        audio_file=audio.strip(),
                        text=msg_text.strip()
                    ))
            except ValueError:
                continue

        return entries

    @staticmethod
    def parse_to_dict(content: bytes) -> dict:
        """
        Parse a .MSG file to a dictionary keyed by message ID.

        Args:
            content: Raw file bytes

        Returns:
            Dict mapping message_id -> MessageEntry
        """
        entries = MsgParser.parse(content)
        return {e.message_id: e for e in entries}

    @staticmethod
    def _read_field(text: str, start_pos: int) -> Tuple[Optional[str], int]:
        """Read a single {}-delimited field."""
        pos = start_pos

        # Find opening brace
        while pos < len(text) and text[pos] != '{':
            if text[pos] == '}':
                return None, pos
            pos += 1

        if pos >= len(text):
            return None, pos

        pos += 1  # Skip '{'

        # Read until closing brace, stripping newlines
        result = []
        while pos < len(text):
            ch = text[pos]
            if ch == '}':
                return ''.join(result), pos + 1
            if ch not in '\n\r':
                result.append(ch)
            pos += 1

        return None, pos  # Unterminated field


class ScriptsListParser:
    """
    Parser for scripts.lst file.

    Format:
        scriptname.int # optional comment
        scriptname2.int

    One script per line, index is line number (0-based).

    Note: Uses split('\\n') to match the game engine's db_fgets() behavior,
    which reads lines terminated by \\n only. The file uses CRLF (\\r\\n) line
    endings, so each line will have a trailing \\r which we strip.
    """

    @staticmethod
    def parse(content: bytes) -> List[Tuple[int, str]]:
        """
        Parse scripts.lst file.

        Args:
            content: Raw file bytes

        Returns:
            List of (index, script_name) tuples
        """
        scripts = []
        # Use split('\n') to match game engine's db_fgets() behavior
        lines = content.decode('ascii', errors='replace').split('\n')

        for index, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # Remove comments
            comment_pos = line.find('#')
            if comment_pos != -1:
                line = line[:comment_pos].strip()

            if not line:
                continue

            # Remove .int extension
            name = line
            dot_pos = name.find('.')
            if dot_pos != -1:
                name = name[:dot_pos]

            scripts.append((index, name.lower()))

        return scripts

    @staticmethod
    def parse_to_dict(content: bytes) -> dict:
        """
        Parse scripts.lst to a dictionary.

        Args:
            content: Raw file bytes

        Returns:
            Dict mapping index -> script_name
        """
        scripts = ScriptsListParser.parse(content)
        return {idx: name for idx, name in scripts}

    @staticmethod
    def parse_name_to_index(content: bytes) -> dict:
        """
        Parse scripts.lst to a name->index dictionary.

        Args:
            content: Raw file bytes

        Returns:
            Dict mapping script_name -> index
        """
        scripts = ScriptsListParser.parse(content)
        return {name: idx for idx, name in scripts}


class ProtoParser:
    """
    Parser for Fallout .PRO prototype files.

    Focuses on critter prototypes to extract:
    - Gender from baseStats[STAT_GENDER]
    - Script ID for linking to scripts
    - Message ID for name/description lookup in pro_crit.msg
    """

    # Header size for critter protos (before CritterProtoData)
    CRITTER_HEADER_SIZE = 44  # 11 x 4-byte ints

    @staticmethod
    def parse_critter(content: bytes) -> Optional[CritterProto]:
        """
        Parse a critter .PRO file.

        Args:
            content: Raw .PRO file bytes

        Returns:
            CritterProto object or None if parsing fails
        """
        if content is None or len(content) < 188:  # Minimum size for critter proto
            return None

        try:
            # Parse header (44 bytes)
            pid, msg_id, fid = struct.unpack('>iii', content[0:12])
            light_dist, light_int, flags, ext_flags = struct.unpack('>iiii', content[12:28])
            sid, head_fid, ai_packet, team = struct.unpack('>iiii', content[28:44])

            # Verify this is a critter (type 1)
            pid_type = (pid >> 24) & 0xFF
            if pid_type != OBJ_TYPE_CRITTER:
                return None

            # Parse CritterProtoData
            # Starts at offset 44
            data_flags = struct.unpack('>i', content[44:48])[0]
            base_stats = struct.unpack('>35i', content[48:188])  # 35 int32s = 140 bytes
            # bonus_stats would be at 188:328, skills at 328:400
            # For now we just need gender from base_stats

            # Read remaining fields (after bonus_stats and skills)
            # offset 188: bonus_stats[35] = 140 bytes -> ends at 328
            # offset 328: skills[18] = 72 bytes -> ends at 400
            # offset 400: bodyType, experience, killType (3 x 4 bytes) = 12 bytes
            if len(content) >= 412:
                body_type, experience, kill_type = struct.unpack('>iii', content[400:412])
            else:
                body_type, experience, kill_type = 0, 0, 0

            # Extract gender from base stats
            gender = base_stats[STAT_GENDER] if len(base_stats) > STAT_GENDER else 0

            # Extract script index from SID
            # SID format: (type << 24) | index
            if sid >= 0:
                script_index = sid & 0x00FFFFFF
            else:
                script_index = -1

            return CritterProto(
                pid=pid,
                message_id=msg_id,
                fid=fid,
                sid=sid,
                script_index=script_index,
                gender=gender,
                head_fid=head_fid,
                ai_packet=ai_packet,
                team=team,
                body_type=body_type,
                experience=experience,
                kill_type=kill_type,
            )

        except struct.error:
            return None

    @staticmethod
    def load_all_critters(dat: 'DATArchive') -> Dict[int, CritterProto]:
        """
        Load all critter prototypes from a DAT archive.

        Args:
            dat: Open DATArchive

        Returns:
            Dict mapping script_index -> CritterProto (only critters with scripts)
        """
        result = {}

        # Read critters list
        critters_lst = dat.read_file('PROTO\\CRITTERS\\CRITTERS.LST')
        if not critters_lst:
            return result

        lines = critters_lst.decode('utf-8', errors='replace').strip().split('\n')

        for line in lines:
            pro_file = line.strip()
            if not pro_file:
                continue

            content = dat.read_file(f'PROTO\\CRITTERS\\{pro_file}')
            proto = ProtoParser.parse_critter(content)

            if proto and proto.has_script:
                result[proto.script_index] = proto

        return result

    @staticmethod
    def load_critter_messages(dat: 'DATArchive', language: str = 'english') -> Dict[int, Tuple[str, str]]:
        """
        Load critter names and descriptions from pro_crit.msg.

        Args:
            dat: Open DATArchive
            language: Language folder name

        Returns:
            Dict mapping message_id -> (name, description)
        """
        result = {}

        paths_to_try = [
            f'TEXT\\{language.upper()}\\GAME\\PRO_CRIT.MSG',
            f'text/{language}/game/pro_crit.msg',
        ]

        content = None
        for path in paths_to_try:
            content = dat.read_file(path)
            if content:
                break

        if not content:
            return result

        messages = MsgParser.parse_to_dict(content)

        # Group by base message ID (multiples of 100)
        # X00 = name, X01 = description
        for msg_id, entry in messages.items():
            base_id = (msg_id // 100) * 100
            if msg_id == base_id:
                # This is a name
                if base_id not in result:
                    result[base_id] = (entry.text, '')
                else:
                    result[base_id] = (entry.text, result[base_id][1])
            elif msg_id == base_id + 1:
                # This is a description
                if base_id not in result:
                    result[base_id] = ('', entry.text)
                else:
                    result[base_id] = (result[base_id][0], entry.text)

        return result

    @staticmethod
    def build_script_to_critter_map(
        dat: 'DATArchive',
        language: str = 'english'
    ) -> Dict[str, Dict]:
        """
        Build a map from script names to critter info (gender, description, name).

        Args:
            dat: Open DATArchive
            language: Language for descriptions

        Returns:
            Dict mapping script_name -> {
                'gender': str,
                'name': str,
                'description': str,
                'proto_pid': int
            }
        """
        result = {}

        # Load script index -> name mapping
        scripts_data = dat.read_file('SCRIPTS\\SCRIPTS.LST')
        if not scripts_data:
            return result
        script_names = ScriptsListParser.parse_to_dict(scripts_data)

        # Load all critter protos
        critters = ProtoParser.load_all_critters(dat)

        # Load critter messages
        messages = ProtoParser.load_critter_messages(dat, language)

        # Build the map
        for script_index, proto in critters.items():
            script_name = script_names.get(script_index)
            if not script_name:
                continue

            name, description = messages.get(proto.message_id, ('', ''))

            result[script_name] = {
                'gender': proto.gender_str,
                'name': name,
                'description': description,
                'proto_pid': proto.pid,
                'message_id': proto.message_id,
            }

        return result

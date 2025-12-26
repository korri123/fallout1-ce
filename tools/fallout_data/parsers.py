"""
File format parsers for Fallout 1 data files.

Includes parsers for:
- .MSG files (dialogue/message text)
- scripts.lst (script index)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

__all__ = [
    'MessageEntry', 'MsgParser',
    'ScriptsListParser',
]


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
        lines = content.decode('ascii', errors='replace').splitlines()

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

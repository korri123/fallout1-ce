"""
Fallout 1 Data File Library

A Python library for reading Fallout 1 game data files, including:
- DAT archives (master.dat, critter.dat)
- MSG files (dialogue/messages)
- Scripts list

Simple Usage:
    from fallout_data import DATArchive, MsgParser

    # Read files from a DAT archive
    with DATArchive('/path/to/master.dat') as dat:
        # List all MSG files
        msg_files = dat.list_files('*.MSG')

        # Read a specific file
        content = dat.read_file('text/english/dialog/aradesh.msg')

        # Parse dialogue
        messages = MsgParser.parse(content)
        for msg in messages:
            print(f"[{msg.message_id}] {msg.text}")

    # Or use the convenience functions
    from fallout_data import read_dat_file, parse_msg

    content = read_dat_file('/path/to/master.dat', 'scripts/scripts.lst')

Advanced Usage:
    from fallout_data import LZSSDecoder

    # Manual LZSS decompression
    decoder = LZSSDecoder()
    decompressed = decoder.decode(compressed_data, expected_size)
"""

from .lzss import LZSSDecoder, decompress, decompress_stream
from .dat import DATArchive, DATEntry
from .parsers import MessageEntry, MsgParser, ScriptsListParser, CritterProto, ProtoParser
from .script import (
    Opcode, ValueType, ProcedureFlags,
    Procedure, Instruction, Script, ScriptIterator
)

__all__ = [
    # LZSS decompression
    'LZSSDecoder',
    'decompress',
    'decompress_stream',

    # DAT archive
    'DATArchive',
    'DATEntry',

    # File parsers
    'MessageEntry',
    'MsgParser',
    'ScriptsListParser',
    'CritterProto',
    'ProtoParser',

    # Script bytecode
    'Opcode',
    'ValueType',
    'ProcedureFlags',
    'Procedure',
    'Instruction',
    'Script',
    'ScriptIterator',

    # Convenience functions
    'read_dat_file',
    'parse_msg',
    'parse_scripts_list',
]

__version__ = '1.0.0'


def read_dat_file(dat_path: str, file_path: str) -> bytes:
    """
    Convenience function to read a single file from a DAT archive.

    Args:
        dat_path: Path to the .dat file
        file_path: Path within the archive

    Returns:
        File contents as bytes

    Raises:
        FileNotFoundError: If file not found in archive
    """
    with DATArchive(dat_path) as dat:
        content = dat.read_file(file_path)
        if content is None:
            raise FileNotFoundError(f"File not found in archive: {file_path}")
        return content


def parse_msg(content: bytes) -> dict:
    """
    Convenience function to parse MSG file to a dictionary.

    Args:
        content: Raw MSG file bytes

    Returns:
        Dict mapping message_id -> MessageEntry
    """
    return MsgParser.parse_to_dict(content)


def parse_scripts_list(content: bytes) -> dict:
    """
    Convenience function to parse scripts.lst to a dictionary.

    Args:
        content: Raw scripts.lst bytes

    Returns:
        Dict mapping index -> script_name
    """
    return ScriptsListParser.parse_to_dict(content)

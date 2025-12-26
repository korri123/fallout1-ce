"""
Fallout 1 DAT archive reader.

DAT files are the primary archive format for Fallout 1 game data.
They use an associative array (hash table) structure for the file index.

Structure:
- Root assoc_array: contains directory names
- Per-directory assoc_array: contains file entries with offset/size/flags
- File data: raw or LZSS compressed, possibly chunked

Compression flags:
- 0x10: Fully LZSS compressed
- 0x20: Uncompressed (raw data)
- 0x40: Chunked (mixed compressed/raw chunks)

Chunked format:
- 2-byte big-endian header per chunk
- If high bit set (0x8000): raw chunk, header & 0x7FFF = bytes to copy
- If high bit clear: LZSS chunk, header = compressed input byte count
"""

import struct
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional

from .lzss import LZSSDecoder

__all__ = ['DATArchive', 'DATEntry']


class DATEntry:
    """Represents a file entry in a DAT archive."""

    FLAG_LZSS = 0x10      # Fully LZSS compressed
    FLAG_RAW = 0x20       # Uncompressed
    FLAG_CHUNKED = 0x40   # Mixed chunks

    def __init__(self, path: str, flags: int, offset: int,
                 packed_size: int, unpacked_size: int):
        self.path = path
        self.flags = flags
        self.offset = offset
        self.packed_size = packed_size
        self.unpacked_size = unpacked_size

    @property
    def is_compressed(self) -> bool:
        """True if file uses any form of compression."""
        return (self.flags & 0xF0) in (self.FLAG_LZSS, self.FLAG_CHUNKED)

    @property
    def compression_type(self) -> str:
        """Human-readable compression type."""
        flag = self.flags & 0xF0
        if flag == self.FLAG_RAW:
            return 'raw'
        elif flag == self.FLAG_LZSS:
            return 'lzss'
        elif flag == self.FLAG_CHUNKED:
            return 'chunked'
        else:
            return f'unknown({flag:#x})'

    def __repr__(self) -> str:
        return (f"DATEntry({self.path!r}, {self.compression_type}, "
                f"offset={self.offset}, size={self.unpacked_size})")


class DATArchive:
    """
    Reader for Fallout 1 DAT archives.

    Usage:
        with DATArchive('/path/to/master.dat') as dat:
            content = dat.read_file('scripts/scripts.lst')
            files = dat.list_files('*.MSG')
    """

    def __init__(self, filepath: str):
        """
        Initialize DAT archive reader.

        Args:
            filepath: Path to the .dat file
        """
        self.filepath = Path(filepath)
        self._file: Optional[BinaryIO] = None
        self._entries: Dict[str, DATEntry] = {}
        self._decoder = LZSSDecoder()

    def open(self):
        """Open and parse the DAT archive."""
        self._file = open(self.filepath, 'rb')
        self._parse_index()

    def close(self):
        """Close the archive."""
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def entries(self) -> Dict[str, DATEntry]:
        """Dictionary of all file entries, keyed by uppercase path."""
        return self._entries

    def _read_u32_be(self) -> int:
        """Read big-endian 32-bit unsigned integer."""
        data = self._file.read(4)
        if len(data) < 4:
            return 0
        return struct.unpack('>I', data)[0]

    def _read_u16_be(self) -> int:
        """Read big-endian 16-bit unsigned integer."""
        data = self._file.read(2)
        if len(data) < 2:
            return 0
        return struct.unpack('>H', data)[0]

    def _read_key(self) -> str:
        """Read a length-prefixed key string."""
        length_byte = self._file.read(1)
        if not length_byte:
            return ""
        length = length_byte[0]
        key_data = self._file.read(length)
        return key_data.decode('ascii', errors='replace')

    def _parse_index(self):
        """Parse the DAT file index structure."""
        # Root assoc_array header
        root_count = self._read_u32_be()
        _root_max = self._read_u32_be()
        root_datasize = self._read_u32_be()
        _root_unused = self._read_u32_be()

        if root_count == 0:
            return

        # Read directory names
        directories = []
        for _ in range(root_count):
            dir_name = self._read_key()
            if root_datasize > 0:
                self._file.read(root_datasize)  # Skip any root-level data
            directories.append(dir_name)

        # Read each directory's file entries
        for dir_name in directories:
            dir_count = self._read_u32_be()
            _dir_max = self._read_u32_be()
            dir_datasize = self._read_u32_be()
            _dir_unused = self._read_u32_be()

            for _ in range(dir_count):
                filename = self._read_key()

                if dir_datasize == 16:  # sizeof(dir_entry)
                    flags = self._read_u32_be()
                    offset = self._read_u32_be()
                    # In C++: de.length = unpacked size, de.field_C = packed size
                    unpacked_size = self._read_u32_be()
                    packed_size = self._read_u32_be()

                    # Build full path
                    if dir_name:
                        full_path = f"{dir_name}\\{filename}"
                    else:
                        full_path = filename

                    entry = DATEntry(
                        path=full_path,
                        flags=flags,
                        offset=offset,
                        packed_size=packed_size,
                        unpacked_size=unpacked_size
                    )
                    self._entries[full_path.upper()] = entry
                elif dir_datasize > 0:
                    self._file.read(dir_datasize)

    def read_file(self, path: str) -> Optional[bytes]:
        """
        Read and decompress a file from the archive.

        Args:
            path: File path within the archive (case-insensitive)

        Returns:
            File contents as bytes, or None if not found
        """
        path_key = path.upper().replace('/', '\\')

        if path_key not in self._entries:
            return None

        entry = self._entries[path_key]
        self._file.seek(entry.offset)

        flag = entry.flags & 0xF0

        if flag == DATEntry.FLAG_RAW:
            return self._file.read(entry.packed_size)

        elif flag == DATEntry.FLAG_LZSS:
            data = self._file.read(entry.packed_size)
            return self._decoder.decode(data, entry.unpacked_size)

        elif flag == DATEntry.FLAG_CHUNKED:
            return self._read_chunked(entry)

        else:
            # Unknown format, try raw
            return self._file.read(entry.packed_size)

    def _read_chunked(self, entry: DATEntry) -> bytes:
        """
        Read a chunked file (mixed compressed/raw chunks).

        Chunked format:
        - 2-byte big-endian header per chunk
        - If high bit set (0x8000): raw chunk, size = header & 0x7FFF
        - If high bit clear: LZSS chunk, header = compressed input byte count
        """
        result = bytearray()
        target_length = entry.unpacked_size

        # Reset decoder for this file
        self._decoder.reset()

        while len(result) < target_length:
            header_data = self._file.read(2)
            if len(header_data) < 2:
                break

            chunk_header = struct.unpack('>H', header_data)[0]

            if chunk_header & 0x8000:
                # Raw chunk - header & 0x7FFF is byte count to copy
                chunk_size = chunk_header & 0x7FFF
                chunk_data = self._file.read(chunk_size)
                result.extend(chunk_data)
                # Update ring buffer with raw data
                self._decoder.update_ring_buffer(chunk_data)
            else:
                # LZSS compressed chunk - header is compressed input byte count
                compressed_size = chunk_header
                decompressed, _ = self._decoder.decode_stream(
                    self._file, compressed_size
                )
                result.extend(decompressed)

        return bytes(result[:target_length])

    def list_files(self, pattern: str = None) -> List[str]:
        """
        List files in the archive.

        Args:
            pattern: Optional filter pattern (case-insensitive substring match)

        Returns:
            List of file paths
        """
        files = list(self._entries.keys())
        if pattern:
            pattern_upper = pattern.upper().replace('/', '\\')
            # Handle glob-like patterns
            if pattern_upper.startswith('*.'):
                ext = pattern_upper[1:]  # Keep the dot
                files = [f for f in files if f.endswith(ext)]
            else:
                files = [f for f in files if pattern_upper in f]
        return sorted(files)

    def get_entry(self, path: str) -> Optional[DATEntry]:
        """
        Get metadata for a file without reading its contents.

        Args:
            path: File path within the archive (case-insensitive)

        Returns:
            DATEntry with file metadata, or None if not found
        """
        path_key = path.upper().replace('/', '\\')
        return self._entries.get(path_key)

    def exists(self, path: str) -> bool:
        """Check if a file exists in the archive."""
        path_key = path.upper().replace('/', '\\')
        return path_key in self._entries

    def extract_file(self, path: str, dest_path: str):
        """
        Extract a file to disk.

        Args:
            path: File path within the archive
            dest_path: Destination path on disk
        """
        content = self.read_file(path)
        if content is None:
            raise FileNotFoundError(f"File not found in archive: {path}")

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    def extract_all(self, dest_dir: str, pattern: str = None):
        """
        Extract all files (optionally filtered) to a directory.

        Args:
            dest_dir: Destination directory
            pattern: Optional filter pattern
        """
        dest = Path(dest_dir)
        files = self.list_files(pattern)

        for file_path in files:
            content = self.read_file(file_path)
            if content:
                # Convert backslashes to forward slashes for path
                rel_path = file_path.replace('\\', '/')
                out_path = dest / rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(content)

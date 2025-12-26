"""
LZSS decompression for Fallout 1 DAT files.

LZSS (Lempel-Ziv-Storer-Szymanski) is a dictionary-based compression algorithm.
Fallout 1 uses a variant with:
- 4096 byte ring buffer (sliding window)
- Initial buffer filled with spaces, write position at 4078 (DICT_SIZE - MAX_MATCH)
- Flag byte controls 8 chunks: bit=1 means literal, bit=0 means reference
- References: 12-bit offset + 4-bit length (length += 3, so 3-18 bytes)

Chunk format (for chunked files):
- 2-byte big-endian header
- If high bit set (0x8000): raw chunk, size = header & 0x7FFF bytes to copy
- If high bit clear: LZSS chunk, header = compressed input byte count
"""

from typing import BinaryIO, Tuple

__all__ = ['LZSSDecoder', 'decompress', 'decompress_stream']


class LZSSDecoder:
    """
    LZSS decoder for Fallout 1 DAT files.

    The ring buffer state persists across chunks for chunked files,
    allowing proper decompression of files split into multiple compressed blocks.
    """

    RING_BUFFER_SIZE = 4096
    RING_BUFFER_FILL = 4078  # Initial write position

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset the decoder state (ring buffer)."""
        self.ring_buffer = bytearray(b' ' * self.RING_BUFFER_SIZE)
        self.ring_index = self.RING_BUFFER_FILL

    def decode(self, data: bytes, compressed_length: int) -> bytes:
        """
        Decode LZSS compressed data by consuming a specified number of input bytes.

        Args:
            data: Compressed input bytes
            compressed_length: Number of compressed input bytes to consume

        Returns:
            Decompressed bytes (variable length based on compression ratio)
        """
        self.reset()

        result = bytearray()
        pos = 0
        bytes_remaining = compressed_length

        while bytes_remaining > 0 and pos < len(data):
            flags = data[pos]
            pos += 1
            bytes_remaining -= 1

            for bit in range(8):
                if bytes_remaining <= 0 or pos >= len(data):
                    break

                if flags & (1 << bit):
                    # Literal byte - consumes 1 input byte
                    byte = data[pos]
                    pos += 1
                    bytes_remaining -= 1
                    result.append(byte)
                    self.ring_buffer[self.ring_index] = byte
                    self.ring_index = (self.ring_index + 1) & 0xFFF
                else:
                    # Dictionary reference - consumes 2 input bytes
                    if bytes_remaining < 2 or pos + 1 >= len(data):
                        break
                    low = data[pos]
                    high = data[pos + 1]
                    pos += 2
                    bytes_remaining -= 2

                    offset = low | ((high & 0xF0) << 4)
                    length = (high & 0x0F) + 3

                    for i in range(length):
                        dict_index = (offset + i) & 0xFFF
                        byte = self.ring_buffer[dict_index]
                        result.append(byte)
                        self.ring_buffer[self.ring_index] = byte
                        self.ring_index = (self.ring_index + 1) & 0xFFF

        return bytes(result)

    def decode_stream(self, stream: BinaryIO, compressed_length: int) -> Tuple[bytes, int]:
        """
        Decode LZSS from a file stream by consuming a specified number of input bytes.

        This method does NOT reset the ring buffer, allowing it to be used
        for chunked files where state persists across chunks.

        Args:
            stream: File stream positioned at compressed data
            compressed_length: Number of compressed input bytes to consume

        Returns:
            Tuple of (decompressed_data, bytes_consumed_from_stream)
        """
        result = bytearray()
        start_pos = stream.tell()
        bytes_remaining = compressed_length

        while bytes_remaining > 0:
            flag_data = stream.read(1)
            if not flag_data:
                break
            flags = flag_data[0]
            bytes_remaining -= 1

            for bit in range(8):
                if bytes_remaining <= 0:
                    break

                if flags & (1 << bit):
                    # Literal byte - consumes 1 input byte
                    byte_data = stream.read(1)
                    if not byte_data:
                        break
                    byte = byte_data[0]
                    bytes_remaining -= 1
                    result.append(byte)
                    self.ring_buffer[self.ring_index] = byte
                    self.ring_index = (self.ring_index + 1) & 0xFFF
                else:
                    # Dictionary reference - consumes 2 input bytes
                    if bytes_remaining < 2:
                        break
                    ref_data = stream.read(2)
                    if len(ref_data) < 2:
                        break
                    low = ref_data[0]
                    high = ref_data[1]
                    bytes_remaining -= 2

                    offset = low | ((high & 0xF0) << 4)
                    length = (high & 0x0F) + 3

                    for i in range(length):
                        dict_index = (offset + i) & 0xFFF
                        byte = self.ring_buffer[dict_index]
                        result.append(byte)
                        self.ring_buffer[self.ring_index] = byte
                        self.ring_index = (self.ring_index + 1) & 0xFFF

        bytes_consumed = stream.tell() - start_pos
        return bytes(result), bytes_consumed

    def update_ring_buffer(self, data: bytes):
        """
        Update ring buffer with raw (uncompressed) data.

        Used for chunked files where some chunks are raw and some compressed,
        but the ring buffer state must persist.

        Args:
            data: Raw bytes to add to ring buffer
        """
        for byte in data:
            self.ring_buffer[self.ring_index] = byte
            self.ring_index = (self.ring_index + 1) & 0xFFF


def decompress(data: bytes, compressed_length: int = None) -> bytes:
    """
    Convenience function to decompress LZSS data.

    Args:
        data: Compressed input bytes
        compressed_length: Number of compressed bytes to consume (defaults to len(data))

    Returns:
        Decompressed bytes
    """
    decoder = LZSSDecoder()
    if compressed_length is None:
        compressed_length = len(data)
    return decoder.decode(data, compressed_length)


def decompress_stream(stream: BinaryIO, compressed_length: int) -> bytes:
    """
    Convenience function to decompress LZSS data from a stream.

    Args:
        stream: File stream positioned at compressed data
        compressed_length: Number of compressed input bytes to consume

    Returns:
        Decompressed bytes
    """
    decoder = LZSSDecoder()
    data, _ = decoder.decode_stream(stream, compressed_length)
    return data

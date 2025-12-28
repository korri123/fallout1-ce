# Fallout Data Library

A Python library for reading Fallout 1 game data files.

## Quick Start

```python
from fallout_data import DATArchive, MsgParser

# Read dialogue from a DAT archive
with DATArchive('/Applications/Fallout/MASTER.DAT') as dat:
    content = dat.read_file('text/english/dialog/aradesh.msg')
    messages = MsgParser.parse(content)

    for msg in messages:
        print(f"[{msg.message_id}] {msg.text}")
```

## Classes

### DATArchive

Read files from Fallout 1 DAT archives (master.dat, critter.dat).

```python
from fallout_data import DATArchive

with DATArchive('/Applications/Fallout/MASTER.DAT') as dat:
    # List all files
    all_files = dat.list_files()
    print(f"Archive contains {len(all_files)} files")

    # List files matching a pattern
    msg_files = dat.list_files('*.MSG')
    int_files = dat.list_files('*.INT')

    # Check if a file exists
    if dat.exists('scripts/scripts.lst'):
        print("Scripts list found!")

    # Read a file
    content = dat.read_file('scripts/scripts.lst')

    # Get file metadata without reading content
    entry = dat.get_entry('text/english/dialog/aradesh.msg')
    print(f"Size: {entry.unpacked_size}, Compression: {entry.compression_type}")

    # Extract a single file to disk
    dat.extract_file('text/english/dialog/aradesh.msg', './aradesh.msg')

    # Extract all MSG files
    dat.extract_all('./extracted/', pattern='*.MSG')
```

### MsgParser

Parse Fallout .MSG dialogue files.

```python
from fallout_data import DATArchive, MsgParser, MessageEntry

with DATArchive('/Applications/Fallout/MASTER.DAT') as dat:
    content = dat.read_file('text/english/dialog/aradesh.msg')

    # Parse to list of MessageEntry objects
    messages = MsgParser.parse(content)
    for msg in messages:
        print(f"ID: {msg.message_id}")
        print(f"Audio: {msg.audio_file}")
        print(f"Text: {msg.text}")
        print()

    # Parse to dictionary for quick lookup
    msg_dict = MsgParser.parse_to_dict(content)
    if 100 in msg_dict:
        print(msg_dict[100].text)
```

### ScriptsListParser

Parse the scripts.lst index file.

```python
from fallout_data import DATArchive, ScriptsListParser

with DATArchive('/Applications/Fallout/MASTER.DAT') as dat:
    content = dat.read_file('scripts/scripts.lst')

    # Parse to list of (index, name) tuples
    scripts = ScriptsListParser.parse(content)
    for idx, name in scripts[:10]:
        print(f"Script {idx}: {name}")

    # Parse to index -> name dictionary
    idx_to_name = ScriptsListParser.parse_to_dict(content)
    print(f"Script 0: {idx_to_name.get(0)}")

    # Parse to name -> index dictionary
    name_to_idx = ScriptsListParser.parse_name_to_index(content)
    print(f"aradesh index: {name_to_idx.get('aradesh')}")
```

### Script

Parse and disassemble Fallout .INT script bytecode files.

```python
from fallout_data import DATArchive, Script, Opcode

with DATArchive('/Applications/Fallout/MASTER.DAT') as dat:
    # Load a script
    script = Script.from_dat(dat, 'scripts/aradesh.int')

    # List all procedures
    print(f"Script has {len(script.procedures)} procedures:")
    for proc in script.procedures:
        print(f"  {proc.name} (addr={proc.code_address}, args={proc.arg_count})")

    # Find a specific procedure
    talk_proc = script.get_procedure('talk_p_proc')
    if talk_proc:
        print(f"Found talk_p_proc at address {talk_proc.code_address}")

    # Disassemble a procedure
    start_proc = script.get_procedure('start')
    if start_proc:
        instructions = script.disassemble_procedure(start_proc)
        for instr in instructions[:20]:  # First 20 instructions
            print(f"  {instr.offset:04X}: {instr.opcode_name}", end='')
            if instr.operand is not None:
                print(f" {instr.operand}", end='')
            print()

    # Iterate through bytecode manually
    iterator = script.iterate(start_offset=0)
    while iterator.has_more():
        instr = iterator.next()
        if instr.opcode == Opcode.GSAY_MESSAGE:
            print(f"Found gsay_message at {instr.offset:04X}")
```

#### Procedure Flags

```python
from fallout_data import Script, ProcedureFlags

script = Script.from_dat(dat, 'scripts/aradesh.int')

for proc in script.procedures:
    flags = []
    if proc.is_timed:
        flags.append("timed")
    if proc.is_conditional:
        flags.append("conditional")
    if proc.is_exported:
        flags.append("exported")
    if proc.is_critical:
        flags.append("critical")

    if flags:
        print(f"{proc.name}: {', '.join(flags)}")
```

### LZSSDecoder

Low-level LZSS decompression (usually not needed directly).

```python
from fallout_data import LZSSDecoder, decompress

# Decompress raw LZSS data
decoder = LZSSDecoder()
decompressed = decoder.decode(compressed_bytes, len(compressed_bytes))

# Or use the convenience function
decompressed = decompress(compressed_bytes)

# For streaming/chunked decompression
decoder = LZSSDecoder()
# decoder.reset() resets the ring buffer
# decoder.decode_stream() preserves ring buffer state between chunks
# decoder.update_ring_buffer() updates state with raw (uncompressed) data
```

## Convenience Functions

```python
from fallout_data import read_dat_file, parse_msg, parse_scripts_list

# Read a file from DAT without context manager
content = read_dat_file('/Applications/Fallout/MASTER.DAT', 'scripts/scripts.lst')

# Parse MSG content to dictionary
msg_content = read_dat_file('/Applications/Fallout/MASTER.DAT',
                            'text/english/dialog/aradesh.msg')
messages = parse_msg(msg_content)
print(messages[100].text)

# Parse scripts list to dictionary
scripts_content = read_dat_file('/Applications/Fallout/MASTER.DAT',
                                'scripts/scripts.lst')
scripts = parse_scripts_list(scripts_content)
```

## Complete Example: Extract All NPC Dialogue

```python
from fallout_data import DATArchive, MsgParser

def extract_all_dialogue(dat_path: str) -> dict:
    """Extract all dialogue from all MSG files."""
    all_dialogue = {}

    with DATArchive(dat_path) as dat:
        msg_files = dat.list_files('*.MSG')

        for file_path in msg_files:
            if 'DIALOG' not in file_path.upper():
                continue

            content = dat.read_file(file_path)
            if content:
                messages = MsgParser.parse(content)
                # Extract NPC name from path
                name = file_path.split('\\')[-1].replace('.MSG', '')
                all_dialogue[name] = messages

    return all_dialogue

dialogue = extract_all_dialogue('/Applications/Fallout/MASTER.DAT')
print(f"Found dialogue for {len(dialogue)} NPCs")

# Print first line from each NPC
for npc, messages in list(dialogue.items())[:5]:
    if messages:
        print(f"{npc}: {messages[0].text[:60]}...")
```

## Complete Example: Find Scripts Using Specific Opcodes

```python
from fallout_data import DATArchive, Script, Opcode

def find_scripts_using_opcode(dat_path: str, target_opcode: Opcode) -> list:
    """Find all scripts that use a specific opcode."""
    results = []

    with DATArchive(dat_path) as dat:
        script_files = dat.list_files('*.INT')

        for file_path in script_files:
            try:
                script = Script.from_dat(dat, file_path)

                # Search all procedures
                for proc in script.procedures:
                    for instr in script.disassemble_procedure(proc):
                        if instr.opcode == target_opcode:
                            results.append({
                                'script': file_path,
                                'procedure': proc.name,
                                'offset': instr.offset
                            })
                            break  # Found in this procedure
            except Exception as e:
                continue  # Skip problematic scripts

    return results

# Find all scripts that use the PARTY_ADD opcode
matches = find_scripts_using_opcode('/Applications/Fallout/MASTER.DAT',
                                     Opcode.PARTY_ADD)
for match in matches:
    print(f"{match['script']}: {match['procedure']}")
```

## Command-Line Usage

The script module can be run directly:

```bash
# List all scripts in a DAT archive
python -m fallout_data.script /path/to/MASTER.DAT --list

# Show procedures in a script
python -m fallout_data.script /path/to/MASTER.DAT scripts/aradesh.int

# Disassemble a specific procedure
python -m fallout_data.script /path/to/MASTER.DAT scripts/aradesh.int -p talk_p_proc

# Disassemble all procedures
python -m fallout_data.script /path/to/MASTER.DAT scripts/aradesh.int --all
```

## File Formats

### DAT Archive
- Container format with LZSS compression
- Files indexed via hash table structure
- Supports raw, fully compressed, and chunked compression

### MSG Files
- Text-based dialogue format
- Structure: `{id}{audio_file}{text}`
- Encoded in Windows-1252 (CP1252)

### INT Files (Scripts)
- Compiled bytecode for Fallout's VM
- Stack-based virtual machine with 340+ opcodes
- Contains procedures, identifiers, and static strings

### scripts.lst
- Plain text index of script files
- One script per line, index is line number
- Format: `scriptname.int # optional comment`

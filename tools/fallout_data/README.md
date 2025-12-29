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

### MapParser

Parse Fallout .MAP files to extract placed objects (NPCs, items, scenery) and scripts.

```python
from fallout_data import DATArchive, MapParser, ObjectType, ScriptType

with DATArchive('/Applications/Fallout/MASTER.DAT') as dat:
    # Load prototype types first (required for accurate parsing)
    item_types, scenery_types = MapParser.load_proto_types(dat)
    parser = MapParser(proto_item_types=item_types, proto_scenery_types=scenery_types)

    # Read and parse a map file
    map_bytes = dat.read_file('MAPS\\JUNKENT.MAP')
    map_data = parser.parse(map_bytes)

    print(f"Map: {map_data.header.name}")
    print(f"Entry tile: {map_data.header.entering_tile}")
    print(f"Total objects: {len(map_data.objects)}")
    print(f"Total scripts: {len(map_data.scripts)}")

    # Get all critters (NPCs)
    critters = [obj for obj in map_data.objects if obj.object_type_raw == ObjectType.CRITTER]
    for critter in critters:
        print(f"Critter PID=0x{critter.pid:08X} at tile {critter.tile}")
        if critter.critter_data:
            print(f"  HP: {critter.critter_data.hp}")
            print(f"  Team: {critter.critter_data.combat.team}")

    # Get objects by elevation
    for obj in map_data.objects_by_elevation[0]:
        if obj.object_type:
            print(f"{obj.object_type.name}: tile={obj.tile}")

    # Access scripts by type
    for script in map_data.critter_scripts:
        print(f"Critter script: idx={script.scr_script_idx}, owner_id={script.scr_oid}")

    # Find the script for a specific object
    for critter in map_data.critters:
        script = map_data.get_script_for_object(critter)
        if script:
            print(f"Critter {critter.id} uses script idx={script.scr_script_idx}")
```

#### MapObject Properties

```python
from fallout_data import MapParser, ObjectType

# Each MapObject has these key properties:
obj = map_data.objects[0]

# Type information
obj.object_type      # ObjectType enum or None if invalid
obj.object_type_raw  # Raw type value (0-5 for valid types)
obj.pid              # Prototype ID (full)
obj.fid              # Frame/art ID

# Position
obj.tile             # Hex tile number
obj.elevation        # Map elevation (0-2)
obj.x, obj.y         # Pixel coordinates
obj.rotation         # Facing direction (0-5)

# Script
obj.has_script       # True if object has an attached script
obj.sid              # Script ID (full)
obj.script_id_number # Script index in scripts.lst (extracted from sid)
obj.message_list_index  # Index for message list lookups

# Type-specific data (depending on object type)
obj.critter_data     # CritterData for critters (HP, combat data)
obj.weapon_data      # WeaponData for weapons
obj.ammo_data        # AmmoData for ammo
obj.door_data        # DoorData for doors
obj.stairs_data      # StairsData for stairs
obj.exit_grid_data   # ExitGridData for map exits

# Inventory (for containers and critters)
obj.inventory_length # Number of inventory items
obj.inventory        # List of InventoryItem
```

#### MapScript Properties

```python
from fallout_data import MapParser, ScriptType

# Each MapScript has these key properties:
script = map_data.scripts[0]

# Type and identification
script.scr_id           # Full script ID (type in high byte)
script.script_type      # ScriptType enum (SYSTEM, SPATIAL, TIMED, ITEM, CRITTER)
script.script_type_raw  # Raw type value (0-4)
script.scr_script_idx   # Index in scripts.lst (use to look up script name)
script.scr_oid          # Owner object ID (matches MapObject.id)

# Spatial script properties (for trigger zones)
script.is_spatial       # True if this is a spatial trigger script
script.built_tile       # Raw tile data (includes elevation)
script.tile             # Tile number (extracted from built_tile)
script.elevation        # Elevation (extracted from built_tile)
script.radius           # Trigger radius in tiles

# Timed script properties
script.is_timed         # True if this is a timed script
script.time             # Execution time

# Other properties
script.scr_flags        # Script flags
script.fixed_param      # Fixed parameter passed to script
script.scr_local_var_offset  # Offset into map's local variables
script.scr_num_local_vars    # Number of local variables

# Helper properties
script.is_critter       # True if CRITTER type
script.is_item          # True if ITEM type
script.script_id_number # Lower 24 bits of scr_id
```

#### Map Script Access

```python
# Access scripts grouped by type
map_data.scripts              # All scripts (list)
map_data.scripts_by_type      # Dict[int, List[MapScript]]
map_data.critter_scripts      # Critter scripts (convenience property)
map_data.item_scripts         # Item scripts
map_data.spatial_scripts      # Spatial trigger scripts

# Find script for an object
script = map_data.get_script_for_object(critter)  # By object reference

# Find scripts by scripts.lst index
scripts = map_data.get_scripts_by_index(36)  # All scripts using index 36
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

## Complete Example: Extract All NPCs From All Maps

```python
from fallout_data import DATArchive, MapParser, ScriptsListParser

def extract_all_npcs(dat_path: str) -> dict:
    """Extract all NPCs (critters) from all maps with their script info."""
    all_npcs = {}

    with DATArchive(dat_path) as dat:
        # Load script name mapping
        scripts_data = dat.read_file('SCRIPTS\\SCRIPTS.LST')
        script_names = ScriptsListParser.parse_to_dict(scripts_data) if scripts_data else {}

        # Load proto types for complete parsing
        item_types, scenery_types = MapParser.load_proto_types(dat)
        parser = MapParser(proto_item_types=item_types, proto_scenery_types=scenery_types)

        # Parse all maps
        map_files = MapParser.list_maps(dat)

        for map_path in map_files:
            try:
                map_data = parser.parse_from_dat(dat, map_path)
                map_name = map_data.header.name or map_path.split('\\')[-1]

                npcs = []
                for critter in map_data.critters:
                    # Find the script for this critter using the new API
                    script = map_data.get_script_for_object(critter)
                    script_name = None
                    script_param = None

                    if script:
                        script_name = script_names.get(script.scr_script_idx)
                        script_param = script.fixed_param

                    npc_info = {
                        'id': critter.id,
                        'pid': critter.pid,
                        'tile': critter.tile,
                        'elevation': critter.elevation,
                        'script_name': script_name,
                        'script_param': script_param,
                    }

                    if critter.critter_data:
                        npc_info['hp'] = critter.critter_data.hp
                        npc_info['team'] = critter.critter_data.combat.team

                    npcs.append(npc_info)

                if npcs:
                    all_npcs[map_name] = npcs

            except Exception as e:
                continue  # Skip problematic maps

    return all_npcs

# Extract and display NPCs
npcs = extract_all_npcs('/Applications/Fallout/MASTER.DAT')
for map_name, critters in list(npcs.items())[:5]:
    print(f"\n{map_name}: {len(critters)} NPCs")
    for npc in critters[:3]:
        script = npc.get('script_name', 'none')
        param = npc.get('script_param')
        param_str = f" (param={param})" if param else ""
        print(f"  PID=0x{npc['pid']:08X}, script={script}{param_str}, tile={npc['tile']}")
```

## Command-Line Usage

The script and map modules can be run directly:

```bash
# === Script Module ===

# List all scripts in a DAT archive
python -m fallout_data.script /path/to/MASTER.DAT --list

# Show procedures in a script
python -m fallout_data.script /path/to/MASTER.DAT scripts/aradesh.int

# Disassemble a specific procedure
python -m fallout_data.script /path/to/MASTER.DAT scripts/aradesh.int -p talk_p_proc

# Disassemble all procedures
python -m fallout_data.script /path/to/MASTER.DAT scripts/aradesh.int --all

# === Map Module ===

# List all maps in a DAT archive
python -m fallout_data.map /path/to/MASTER.DAT --list

# Parse a map and show summary
python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map

# Show only critters (NPCs)
python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --critters

# Show only objects with scripts
python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --scripted

# Show all scripts on a map (grouped by type)
python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --scripts

# Show detailed script info
python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --scripts --verbose

# Show detailed object info
python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --critters --verbose

# Filter by elevation
python -m fallout_data.map /path/to/MASTER.DAT maps/vault13.map --elevation 1
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

### MAP Files
- Binary map format containing placed objects and scripts
- Structure:
  - Header (236 bytes): version, name, entry point, variable counts, flags
  - Global variables (count * 4 bytes)
  - Local variables (count * 4 bytes)
  - Tile data: floor/roof tiles per elevation (10000 tiles * 4 bytes each, skipped if elevation empty)
  - Scripts section: script metadata grouped by type
  - Objects section: all placed objects by elevation
- Script section format:
  - 5 script types (SYSTEM, SPATIAL, TIMED, ITEM, CRITTER)
  - For each type: count, then extents of 16 scripts each
  - Per script: scr_id, flags, script_idx, owner_object_id, local vars, etc.
  - Spatial scripts include tile and trigger radius
  - Timed scripts include execution time
- Object format (per object):
  - 18 int32s: id, tile, position, fid, flags, pid, sid, etc.
  - Type-specific data based on PID type (critter HP, door flags, etc.)
  - Inventory items (recursive object data)
- All integers are big-endian

### scripts.lst
- Plain text index of script files
- One script per line, index is line number
- Format: `scriptname.int # optional comment`

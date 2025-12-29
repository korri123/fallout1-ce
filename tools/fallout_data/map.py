"""
Fallout 1 Map File Parser.

Parses .MAP files from Fallout 1, which contain placed objects on maps.
Map files are stored in the maps/ directory within MASTER.DAT.

File Structure:
- Map header (MapHeader): version, name, entry point, variables, etc.
- Tile data: floor/roof tiles for each elevation (handled separately)
- Scripts: script metadata (handled separately)
- Objects: all placed objects organized by elevation

Object Format:
Each object is serialized with:
- 18 int32s of base object data (id, tile, position, fid, flags, pid, etc.)
- Type-specific "proto update data" based on PID type:
  - Critters: combat data, HP, radiation, poison
  - Items: weapon ammo, ammo quantity, misc charges, key codes
  - Scenery: door flags, stairs destination, elevator info
  - Misc: exit grid data
- Inventory items (recursive object loading)

All integers are big-endian in the file format.
"""

import struct
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import BinaryIO, Dict, List, Optional, Tuple, TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .dat import DATArchive

__all__ = [
    'ObjectType', 'ItemType', 'SceneryType', 'ObjectFlags', 'ScriptType',
    'CombatData', 'InventoryItem', 'MapObject', 'MapHeader', 'MapScript',
    'MapParser',
]


# =============================================================================
# Constants and Enums
# =============================================================================

class ObjectType(IntEnum):
    """Object type, extracted from PID high byte."""
    ITEM = 0
    CRITTER = 1
    SCENERY = 2
    WALL = 3
    TILE = 4
    MISC = 5


class ItemType(IntEnum):
    """Item subtypes for OBJ_TYPE_ITEM objects."""
    ARMOR = 0
    CONTAINER = 1
    DRUG = 2
    WEAPON = 3
    AMMO = 4
    MISC = 5
    KEY = 6


class SceneryType(IntEnum):
    """Scenery subtypes for OBJ_TYPE_SCENERY objects."""
    DOOR = 0
    STAIRS = 1
    ELEVATOR = 2
    LADDER_UP = 3
    LADDER_DOWN = 4
    GENERIC = 5


class ObjectFlags(IntFlag):
    """Object flags."""
    HIDDEN = 0x01
    NO_SAVE = 0x04
    FLAT = 0x08
    NO_BLOCK = 0x10
    LIGHTING = 0x20
    NO_REMOVE = 0x400
    MULTIHEX = 0x800
    NO_HIGHLIGHT = 0x1000
    USED = 0x2000
    TRANS_RED = 0x4000
    TRANS_NONE = 0x8000
    TRANS_WALL = 0x10000
    TRANS_GLASS = 0x20000
    TRANS_STEAM = 0x40000
    TRANS_ENERGY = 0x80000
    IN_LEFT_HAND = 0x1000000
    IN_RIGHT_HAND = 0x2000000
    WORN = 0x4000000
    WALL_TRANS_END = 0x10000000
    LIGHT_THRU = 0x20000000
    SEEN = 0x40000000
    SHOOT_THRU = 0x80000000


class Rotation(IntEnum):
    """Object rotation/facing direction."""
    NE = 0
    E = 1
    SE = 2
    SW = 3
    W = 4
    NW = 5


class ScriptType(IntEnum):
    """Script types, extracted from SID high byte."""
    SYSTEM = 0   # Map scripts
    SPATIAL = 1  # Location-triggered scripts
    TIMED = 2    # Time-based scripts
    ITEM = 3     # Item scripts
    CRITTER = 4  # Critter scripts


# Elevation count
ELEVATION_COUNT = 3
# Scripts per extent in the file format
SCRIPTS_PER_EXTENT = 16
# Number of script types
SCRIPT_TYPE_COUNT = 5


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class CombatData:
    """Combat state data for critters."""
    damage_last_turn: int = 0
    maneuver: int = 0
    ap: int = 0  # Current action points
    results: int = 0  # Dam flags
    ai_packet: int = 0
    team: int = 0
    who_hit_me_cid: int = -1  # Combat ID of attacker


@dataclass
class CritterData:
    """Critter-specific object data."""
    reaction: int = 0  # Reaction to PC
    combat: CombatData = field(default_factory=CombatData)
    hp: int = 0  # Current hit points
    radiation: int = 0
    poison: int = 0


@dataclass
class WeaponData:
    """Weapon item data."""
    ammo_quantity: int = 0
    ammo_type_pid: int = 0


@dataclass
class AmmoData:
    """Ammo item data."""
    quantity: int = 0


@dataclass
class MiscItemData:
    """Misc item data (like Geiger counter charges)."""
    charges: int = 0


@dataclass
class KeyData:
    """Key item data."""
    key_code: int = 0


@dataclass
class DoorData:
    """Door scenery data."""
    open_flags: int = 0

    @property
    def is_locked(self) -> bool:
        return bool(self.open_flags & 0x02000000)

    @property
    def is_jammed(self) -> bool:
        return bool(self.open_flags & 0x04000000)


@dataclass
class StairsData:
    """Stairs scenery data."""
    destination_map: int = 0
    destination_built_tile: int = 0

    @property
    def destination_tile(self) -> int:
        return self.destination_built_tile & 0x3FFFFFF

    @property
    def destination_elevation(self) -> int:
        return (self.destination_built_tile >> 29) & 0x7


@dataclass
class ElevatorData:
    """Elevator scenery data."""
    type: int = 0
    level: int = 0


@dataclass
class LadderData:
    """Ladder scenery data."""
    destination_built_tile: int = 0

    @property
    def destination_tile(self) -> int:
        return self.destination_built_tile & 0x3FFFFFF

    @property
    def destination_elevation(self) -> int:
        return (self.destination_built_tile >> 29) & 0x7


@dataclass
class ExitGridData:
    """Exit grid (misc object) data for map transitions."""
    map: int = 0
    tile: int = 0
    elevation: int = 0
    rotation: int = 0


@dataclass
class InventoryItem:
    """An item in an object's inventory."""
    quantity: int
    item: 'MapObject'


@dataclass
class MapScript:
    """
    A script entry from a map file.

    Scripts are attached to objects (critters, items) or locations (spatial)
    or triggered by time (timed) or are map-level (system).
    """
    # Core identification
    scr_id: int = 0           # Script ID (type in high byte, number in low bytes)
    scr_next: int = 0         # Unused chain pointer

    # Type-specific data (union in C)
    # For SPATIAL scripts:
    built_tile: int = 0       # Tile location for spatial scripts
    radius: int = 0           # Trigger radius for spatial scripts
    # For TIMED scripts:
    time: int = 0             # Execution time for timed scripts

    # Common fields
    scr_flags: int = 0        # Script flags
    scr_script_idx: int = 0   # Index into scripts.lst
    scr_oid: int = -1         # Object ID that owns this script
    scr_local_var_offset: int = 0  # Offset into map's local variables
    scr_num_local_vars: int = 0    # Number of local variables
    field_28: int = 0         # Return value or similar
    action: int = 0           # Current action
    fixed_param: int = 0      # Parameter passed to script
    action_being_used: int = 0
    script_overrides: int = 0
    field_48: int = 0
    how_much: int = 0
    run_info_flags: int = 0

    @property
    def script_type(self) -> Optional[ScriptType]:
        """Get the script type from the SID."""
        type_val = (self.scr_id >> 24) & 0xFF
        try:
            return ScriptType(type_val)
        except ValueError:
            return None

    @property
    def script_type_raw(self) -> int:
        """Get the raw script type value."""
        return (self.scr_id >> 24) & 0xFF

    @property
    def script_id_number(self) -> int:
        """Get the script ID number (lower 24 bits)."""
        return self.scr_id & 0x00FFFFFF

    @property
    def is_spatial(self) -> bool:
        return self.script_type_raw == ScriptType.SPATIAL

    @property
    def is_timed(self) -> bool:
        return self.script_type_raw == ScriptType.TIMED

    @property
    def is_critter(self) -> bool:
        return self.script_type_raw == ScriptType.CRITTER

    @property
    def is_item(self) -> bool:
        return self.script_type_raw == ScriptType.ITEM

    @property
    def tile(self) -> int:
        """Get tile from built_tile (for spatial scripts)."""
        return self.built_tile & 0x3FFFFFF

    @property
    def elevation(self) -> int:
        """Get elevation from built_tile (for spatial scripts)."""
        return (self.built_tile >> 29) & 0x7

    def __repr__(self) -> str:
        type_name = self.script_type.name if self.script_type else f"UNKNOWN({self.script_type_raw})"
        return f"MapScript({type_name}, idx={self.scr_script_idx}, oid={self.scr_oid})"


@dataclass
class MapObject:
    """
    A placed object on a map.

    Contains both the base object data and type-specific data
    based on the object's PID (prototype ID).
    """
    # Base object data (18 int32s from binary)
    id: int = 0
    tile: int = -1
    x: int = 0
    y: int = 0
    sx: int = 0
    sy: int = 0
    frame: int = 0
    rotation: int = 0
    fid: int = 0  # Frame/art ID
    flags: int = 0
    elevation: int = 0
    pid: int = 0  # Prototype ID
    cid: int = 0  # Combat ID
    light_distance: int = 0
    light_intensity: int = 0
    sid: int = -1  # Script ID
    message_list_index: int = -1  # Index for message list lookups

    # Inventory (common to all types)
    inventory_length: int = 0
    inventory_capacity: int = 0
    inventory: List[InventoryItem] = field(default_factory=list)

    # Type-specific data
    critter_data: Optional[CritterData] = None
    item_flags: int = 0  # Non-critter "updated flags"
    weapon_data: Optional[WeaponData] = None
    ammo_data: Optional[AmmoData] = None
    misc_item_data: Optional[MiscItemData] = None
    key_data: Optional[KeyData] = None
    door_data: Optional[DoorData] = None
    stairs_data: Optional[StairsData] = None
    elevator_data: Optional[ElevatorData] = None
    ladder_data: Optional[LadderData] = None
    exit_grid_data: Optional[ExitGridData] = None

    # Proto reference (loaded separately if needed)
    _proto_item_type: Optional[int] = None
    _proto_scenery_type: Optional[int] = None

    @property
    def object_type(self) -> Optional[ObjectType]:
        """Get the object type from the PID."""
        type_val = (self.pid >> 24) & 0xFF
        try:
            return ObjectType(type_val)
        except ValueError:
            return None

    @property
    def object_type_raw(self) -> int:
        """Get the raw object type value from the PID."""
        return (self.pid >> 24) & 0xFF

    @property
    def pid_id(self) -> int:
        """Get the prototype ID number (without type bits)."""
        return self.pid & 0x00FFFFFF

    @property
    def fid_type(self) -> int:
        """Get the FID type."""
        return (self.fid >> 24) & 0xF

    @property
    def fid_id(self) -> int:
        """Get the FID index."""
        return self.fid & 0xFFF

    @property
    def is_critter(self) -> bool:
        return self.object_type_raw == ObjectType.CRITTER

    @property
    def is_item(self) -> bool:
        return self.object_type_raw == ObjectType.ITEM

    @property
    def is_scenery(self) -> bool:
        return self.object_type_raw == ObjectType.SCENERY

    @property
    def has_script(self) -> bool:
        return self.sid >= 0

    @property
    def script_type(self) -> int:
        """Get script type from SID."""
        if self.sid < 0:
            return -1
        return (self.sid >> 24) & 0xFF

    @property
    def script_id_number(self) -> int:
        """Get script ID number (index in scripts.lst)."""
        if self.sid < 0:
            return -1
        return self.sid & 0x00FFFFFF

    def __repr__(self) -> str:
        type_name = self.object_type.name
        pos = f"tile={self.tile}, elev={self.elevation}"
        return f"MapObject({type_name}, pid=0x{self.pid:08X}, {pos})"


@dataclass
class MapHeader:
    """Map file header."""
    version: int = 0
    name: str = ""
    entering_tile: int = 0
    entering_elevation: int = 0
    entering_rotation: int = 0
    local_variables_count: int = 0
    message_list_index: int = -1
    flags: int = 0
    darkness: int = 0
    global_variables_count: int = 0
    map_id: int = 0
    last_visit_time: int = 0

    @property
    def has_map_script(self) -> bool:
        return self.message_list_index >= 0


@dataclass
class Map:
    """Parsed map data."""
    header: MapHeader
    objects: List[MapObject]
    objects_by_elevation: Dict[int, List[MapObject]]
    scripts: List[MapScript] = field(default_factory=list)
    scripts_by_type: Dict[int, List[MapScript]] = field(default_factory=dict)

    @property
    def critters(self) -> List[MapObject]:
        """Get all critter objects."""
        return [obj for obj in self.objects if obj.is_critter]

    @property
    def items(self) -> List[MapObject]:
        """Get all item objects."""
        return [obj for obj in self.objects if obj.is_item]

    @property
    def scenery(self) -> List[MapObject]:
        """Get all scenery objects."""
        return [obj for obj in self.objects if obj.is_scenery]

    @property
    def critter_scripts(self) -> List[MapScript]:
        """Get all critter scripts."""
        return self.scripts_by_type.get(ScriptType.CRITTER, [])

    @property
    def item_scripts(self) -> List[MapScript]:
        """Get all item scripts."""
        return self.scripts_by_type.get(ScriptType.ITEM, [])

    @property
    def spatial_scripts(self) -> List[MapScript]:
        """Get all spatial scripts."""
        return self.scripts_by_type.get(ScriptType.SPATIAL, [])

    def get_objects_at_tile(self, tile: int, elevation: int = 0) -> List[MapObject]:
        """Get all objects at a specific tile."""
        return [obj for obj in self.objects_by_elevation.get(elevation, [])
                if obj.tile == tile]

    def get_script_for_object(self, obj: MapObject) -> Optional[MapScript]:
        """Find the script associated with a map object by matching scr_oid to object id."""
        for script in self.scripts:
            if script.scr_oid == obj.id:
                return script
        return None

    def get_scripts_by_index(self, script_idx: int) -> List[MapScript]:
        """Get all scripts with a given scripts.lst index."""
        return [s for s in self.scripts if s.scr_script_idx == script_idx]


# =============================================================================
# Parser
# =============================================================================

class MapParser:
    """
    Parser for Fallout .MAP files.

    Parses the map header and all placed objects from a map file.
    Objects are loaded with their type-specific data based on their PIDs.

    Note: To fully interpret item/scenery subtypes, you need access to the
    prototype files (.PRO). The parser can work without them but won't know
    the specific item/scenery types.

    Usage:
        from fallout_data import DATArchive, MapParser

        with DATArchive('/path/to/MASTER.DAT') as dat:
            parser = MapParser()
            map_data = parser.parse_from_dat(dat, 'maps/junktown.map')

            print(f"Map: {map_data.header.name}")
            print(f"Objects: {len(map_data.objects)}")

            for critter in map_data.critters:
                print(f"  Critter PID={critter.pid:08X} at tile {critter.tile}")
    """

    # Map header size
    HEADER_SIZE = 236  # 4+16+4*10+176 = 236 bytes

    def __init__(self, proto_item_types: Optional[Dict[int, int]] = None,
                 proto_scenery_types: Optional[Dict[int, int]] = None):
        """
        Initialize parser with optional prototype type mappings.

        Args:
            proto_item_types: Dict mapping item PID -> ItemType
            proto_scenery_types: Dict mapping scenery PID -> SceneryType
        """
        self._proto_item_types = proto_item_types or {}
        self._proto_scenery_types = proto_scenery_types or {}

    @staticmethod
    def load_proto_types(dat: 'DATArchive') -> Tuple[Dict[int, int], Dict[int, int]]:
        """
        Load item and scenery types from prototype files in a DAT archive.

        Args:
            dat: Open DATArchive containing PROTO folder

        Returns:
            Tuple of (item_types, scenery_types) dicts mapping PID -> type
        """
        item_types = {}
        scenery_types = {}

        # Load items - PID is stored at offset 0 in PRO file
        items_lst = dat.read_file('PROTO\\ITEMS\\ITEMS.LST')
        if items_lst:
            lines = items_lst.decode('ascii', errors='replace').strip().split('\n')
            for line in lines:
                pro_file = line.strip()
                if not pro_file:
                    continue
                content = dat.read_file(f'PROTO\\ITEMS\\{pro_file}')
                if content and len(content) >= 36:
                    # PID is at offset 0, type at offset 32
                    pid = struct.unpack('>i', content[0:4])[0]
                    item_type = struct.unpack('>i', content[32:36])[0]
                    item_types[pid] = item_type

        # Load scenery - PID is stored at offset 0 in PRO file
        scenery_lst = dat.read_file('PROTO\\SCENERY\\SCENERY.LST')
        if scenery_lst:
            lines = scenery_lst.decode('ascii', errors='replace').strip().split('\n')
            for line in lines:
                pro_file = line.strip()
                if not pro_file:
                    continue
                content = dat.read_file(f'PROTO\\SCENERY\\{pro_file}')
                if content and len(content) >= 36:
                    pid = struct.unpack('>i', content[0:4])[0]
                    scenery_type = struct.unpack('>i', content[32:36])[0]
                    scenery_types[pid] = scenery_type

        return item_types, scenery_types

    def parse(self, data: bytes) -> Map:
        """
        Parse a map file from bytes.

        Args:
            data: Raw map file bytes

        Returns:
            Parsed Map object
        """
        reader = _BinaryReader(data)

        # Parse header
        header = self._read_header(reader)

        # Read scripts and objects sections
        scripts, scripts_by_type, objects, objects_by_elevation = self._read_map_data(data, header)

        return Map(
            header=header,
            objects=objects,
            objects_by_elevation=objects_by_elevation,
            scripts=scripts,
            scripts_by_type=scripts_by_type
        )

    def _read_header(self, reader: '_BinaryReader') -> MapHeader:
        """Read the map header."""
        version = reader.read_int32()
        name = reader.read_bytes(16).rstrip(b'\x00').decode('ascii', errors='replace')
        entering_tile = reader.read_int32()
        entering_elevation = reader.read_int32()
        entering_rotation = reader.read_int32()
        local_vars_count = reader.read_int32()
        message_list_index = reader.read_int32()
        flags = reader.read_int32()
        darkness = reader.read_int32()
        global_vars_count = reader.read_int32()
        map_id = reader.read_int32()
        last_visit_time = reader.read_int32()

        # Skip reserved fields (44 int32s)
        reader.skip(44 * 4)

        return MapHeader(
            version=version,
            name=name,
            entering_tile=entering_tile,
            entering_elevation=entering_elevation,
            entering_rotation=entering_rotation,
            local_variables_count=local_vars_count,
            message_list_index=message_list_index,
            flags=flags,
            darkness=darkness,
            global_variables_count=global_vars_count,
            map_id=map_id,
            last_visit_time=last_visit_time,
        )

    def _read_map_data(self, data: bytes, header: MapHeader) -> Tuple[
            List[MapScript], Dict[int, List[MapScript]],
            List[MapObject], Dict[int, List[MapObject]]]:
        """
        Read the scripts and objects sections of the map.

        Returns:
            Tuple of (scripts, scripts_by_type, objects, objects_by_elevation)
        """
        scripts: List[MapScript] = []
        scripts_by_type: Dict[int, List[MapScript]] = {i: [] for i in range(SCRIPT_TYPE_COUNT)}
        objects: List[MapObject] = []
        objects_by_elevation: Dict[int, List[MapObject]] = {0: [], 1: [], 2: []}

        # File format order:
        # 1. Header (236 bytes)
        # 2. Global variables (count * 4 bytes)
        # 3. Local variables (count * 4 bytes)
        # 4. Tiles (per elevation with flag NOT set: 10000 * 4 bytes)
        # 5. Scripts (variable size)
        # 6. Objects

        offset = self.HEADER_SIZE

        # Skip global variables (immediately after header)
        if header.global_variables_count > 0:
            offset += header.global_variables_count * 4

        # Skip local variables
        if header.local_variables_count > 0:
            offset += header.local_variables_count * 4

        # Skip tile data
        # Elevation flags: elev0=2, elev1=4, elev2=8
        # If flag bit is SET, elevation is EMPTY (no tiles stored)
        # If flag bit is NOT SET, 10000 tiles * 4 bytes are stored
        elevation_flags = [2, 4, 8]
        for elev in range(3):
            if not (header.flags & elevation_flags[elev]):
                offset += 10000 * 4  # SQUARE_GRID_SIZE * sizeof(int32)

        # Read scripts section
        scripts, scripts_by_type, offset = self._read_scripts_section(data, offset)
        if offset < 0:
            return scripts, scripts_by_type, objects, objects_by_elevation

        reader = _BinaryReader(data, offset)

        try:
            total_count = reader.read_int32()
            if total_count < 0 or total_count > 50000:
                return scripts, scripts_by_type, objects, objects_by_elevation

            # Format: total_count, then for each elevation:
            #   elev_count, then elev_count objects
            for elevation in range(ELEVATION_COUNT):
                elev_count = reader.read_int32()
                if elev_count < 0 or elev_count > total_count:
                    break

                for _ in range(elev_count):
                    obj = self._read_object(reader, elevation)
                    if obj:
                        objects.append(obj)
                        objects_by_elevation[elevation].append(obj)

        except (struct.error, IndexError):
            pass  # Partial parse is OK

        return scripts, scripts_by_type, objects, objects_by_elevation

    def _read_scripts_section(self, data: bytes, offset: int) -> Tuple[List[MapScript], Dict[int, List[MapScript]], int]:
        """
        Read the scripts section and return scripts plus the offset where objects begin.

        Script section format:
        - 5 script types (SCRIPT_TYPE_COUNT)
        - For each type:
          - int32: scripts_count
          - If scripts_count > 0:
            - numExtents = ceil(scripts_count / 16)
            - Each extent contains:
              - 16 scripts (SCRIPT_LIST_EXTENT_SIZE)
              - int32: length (actual count of valid scripts in this extent)
              - int32: next (pointer, stored as int, ignored)

        Each script's size depends on its SID_TYPE (from scr_id >> 24):
        - SPATIAL (1): 18 int32s = 72 bytes
        - TIMED (2): 17 int32s = 68 bytes
        - Other (0, 3, 4): 16 int32s = 64 bytes

        Returns:
            Tuple of (all_scripts, scripts_by_type, next_offset)
        """
        all_scripts: List[MapScript] = []
        scripts_by_type: Dict[int, List[MapScript]] = {i: [] for i in range(SCRIPT_TYPE_COUNT)}

        try:
            for script_type in range(SCRIPT_TYPE_COUNT):
                if offset + 4 > len(data):
                    return all_scripts, scripts_by_type, -1

                scripts_count = struct.unpack('>i', data[offset:offset+4])[0]
                offset += 4

                if scripts_count <= 0:
                    continue

                # Calculate number of extents
                num_extents = (scripts_count + SCRIPTS_PER_EXTENT - 1) // SCRIPTS_PER_EXTENT
                scripts_read = 0

                for extent_idx in range(num_extents):
                    # Read 16 script slots (some may be unused in last extent)
                    extent_scripts: List[MapScript] = []

                    for slot_idx in range(SCRIPTS_PER_EXTENT):
                        if offset + 8 > len(data):
                            return all_scripts, scripts_by_type, -1

                        script = MapScript()

                        # Read scr_id and scr_next
                        script.scr_id = struct.unpack('>i', data[offset:offset+4])[0]
                        script.scr_next = struct.unpack('>i', data[offset+4:offset+8])[0]
                        offset += 8

                        # Determine script type from SID
                        sid_type = (script.scr_id >> 24) & 0xFF

                        # Type-specific extra fields
                        if sid_type == ScriptType.SPATIAL:
                            script.built_tile = struct.unpack('>i', data[offset:offset+4])[0]
                            script.radius = struct.unpack('>i', data[offset+4:offset+8])[0]
                            offset += 8
                        elif sid_type == ScriptType.TIMED:
                            script.time = struct.unpack('>i', data[offset:offset+4])[0]
                            offset += 4

                        # Read 14 common fields
                        script.scr_flags = struct.unpack('>i', data[offset:offset+4])[0]
                        script.scr_script_idx = struct.unpack('>i', data[offset+4:offset+8])[0]
                        # Skip program pointer (offset+8 to offset+12)
                        script.scr_oid = struct.unpack('>i', data[offset+12:offset+16])[0]
                        script.scr_local_var_offset = struct.unpack('>i', data[offset+16:offset+20])[0]
                        script.scr_num_local_vars = struct.unpack('>i', data[offset+20:offset+24])[0]
                        script.field_28 = struct.unpack('>i', data[offset+24:offset+28])[0]
                        script.action = struct.unpack('>i', data[offset+28:offset+32])[0]
                        script.fixed_param = struct.unpack('>i', data[offset+32:offset+36])[0]
                        script.action_being_used = struct.unpack('>i', data[offset+36:offset+40])[0]
                        script.script_overrides = struct.unpack('>i', data[offset+40:offset+44])[0]
                        script.field_48 = struct.unpack('>i', data[offset+44:offset+48])[0]
                        script.how_much = struct.unpack('>i', data[offset+48:offset+52])[0]
                        script.run_info_flags = struct.unpack('>i', data[offset+52:offset+56])[0]
                        offset += 14 * 4  # 56 bytes

                        extent_scripts.append(script)

                    # Read extent length and next pointer
                    extent_length = struct.unpack('>i', data[offset:offset+4])[0]
                    # next pointer at offset+4 is ignored
                    offset += 8

                    # Only add valid scripts from this extent
                    for i in range(min(extent_length, SCRIPTS_PER_EXTENT)):
                        script = extent_scripts[i]
                        all_scripts.append(script)
                        scripts_by_type[script_type].append(script)
                        scripts_read += 1

            return all_scripts, scripts_by_type, offset

        except (struct.error, IndexError):
            return all_scripts, scripts_by_type, -1

    def _find_objects_offset(self, data: bytes, start_offset: int) -> int:
        """
        Find the offset where the objects section begins.

        Scans from start_offset looking for a valid objects header pattern.
        The objects section has format:
        - total_count (int32)
        - elev0_count (int32)
        - elev0 objects...
        - elev1_count (int32)
        - elev1 objects...
        - elev2_count (int32)
        - elev2 objects...
        """
        # Scan through the data looking for valid patterns
        max_scan = min(len(data) - 100, start_offset + 200000)

        for offset in range(start_offset, max_scan, 4):
            if offset + 100 > len(data):
                break

            total_count = struct.unpack('>i', data[offset:offset+4])[0]
            if total_count <= 0 or total_count > 10000:
                continue

            # Read first elevation count (immediately after total)
            e0_count = struct.unpack('>i', data[offset+4:offset+8])[0]
            if e0_count < 0 or e0_count > total_count:
                continue

            # First object starts at offset+8 (after total_count and e0_count)
            first_obj_offset = offset + 8

            # If e0_count is 0, the next value should be e1_count
            if e0_count == 0:
                e1_count = struct.unpack('>i', data[first_obj_offset:first_obj_offset+4])[0]
                if e1_count < 0 or e1_count > total_count:
                    continue
                # Continue to check e1's first object or e2
                if e1_count == 0:
                    continue  # Both e0 and e1 empty, skip for simplicity
                first_obj_offset += 4

            # Validate first object
            if first_obj_offset + 72 > len(data):
                continue

            obj_tile = struct.unpack('>i', data[first_obj_offset+4:first_obj_offset+8])[0]

            # PID is at offset 44 in the object (after 11 int32s)
            pid = struct.unpack('>i', data[first_obj_offset+44:first_obj_offset+48])[0]
            pid_type = (pid >> 24) & 0xFF

            # Valid PID type check (0-5)
            if pid_type > 5:
                continue

            # Valid tile check (-1 is valid for items in inventory)
            if obj_tile < -1 or obj_tile > 100000:
                continue

            # Additional validation: check FID at offset 32 (after 8 int32s)
            fid = struct.unpack('>i', data[first_obj_offset+32:first_obj_offset+36])[0]
            fid_type = (fid >> 24) & 0xF
            if fid_type > 10:  # FID types are 0-10
                continue

            # This looks valid
            return offset

        return -1

    def _read_object(self, reader: '_BinaryReader', elevation: int) -> Optional[MapObject]:
        """Read a single object from the stream."""
        try:
            obj = MapObject()

            # Read base object data (18 int32s)
            obj.id = reader.read_int32()
            obj.tile = reader.read_int32()
            obj.x = reader.read_int32()
            obj.y = reader.read_int32()
            obj.sx = reader.read_int32()
            obj.sy = reader.read_int32()
            obj.frame = reader.read_int32()
            obj.rotation = reader.read_int32()
            obj.fid = reader.read_int32()
            obj.flags = reader.read_int32()
            obj.elevation = reader.read_int32()
            obj.pid = reader.read_int32()
            obj.cid = reader.read_int32()
            obj.light_distance = reader.read_int32()
            obj.light_intensity = reader.read_int32()
            _field_74 = reader.read_int32()  # Unused field
            obj.sid = reader.read_int32()
            obj.message_list_index = reader.read_int32()

            # Override elevation from the loop (file stores it but we use loop value)
            obj.elevation = elevation

            # Read proto update data
            self._read_proto_update_data(reader, obj)

            # Read inventory items
            if obj.inventory_length > 0:
                obj.inventory = []
                for _ in range(obj.inventory_length):
                    quantity = reader.read_int32()
                    item_obj = self._read_object(reader, elevation)
                    if item_obj:
                        obj.inventory.append(InventoryItem(quantity=quantity, item=item_obj))

            return obj

        except (struct.error, IndexError):
            return None

    def _read_proto_update_data(self, reader: '_BinaryReader', obj: MapObject) -> None:
        """Read type-specific object data."""
        # Read inventory header (common to all)
        obj.inventory_length = reader.read_int32()
        obj.inventory_capacity = reader.read_int32()
        _items_ptr = reader.read_int32()  # Pointer, meaningless in file

        obj_type = obj.object_type
        obj_type_raw = obj.object_type_raw

        if obj_type_raw == ObjectType.CRITTER:
            # Critter data
            critter = CritterData()
            critter.reaction = reader.read_int32()

            # Combat data
            combat = CombatData()
            combat.damage_last_turn = reader.read_int32()
            combat.maneuver = reader.read_int32()
            combat.ap = reader.read_int32()
            combat.results = reader.read_int32()
            combat.ai_packet = reader.read_int32()
            combat.team = reader.read_int32()
            combat.who_hit_me_cid = reader.read_int32()
            critter.combat = combat

            critter.hp = reader.read_int32()
            critter.radiation = reader.read_int32()
            critter.poison = reader.read_int32()
            obj.critter_data = critter

        else:
            # Non-critter: read flags field
            obj.item_flags = reader.read_int32()

            if obj_type_raw == ObjectType.ITEM:
                self._read_item_data(reader, obj)

            elif obj_type_raw == ObjectType.SCENERY:
                self._read_scenery_data(reader, obj)

            elif obj_type_raw == ObjectType.MISC:
                self._read_misc_data(reader, obj)

    def _read_item_data(self, reader: '_BinaryReader', obj: MapObject) -> None:
        """Read item-specific data based on item subtype."""
        # Get item type from proto mapping or stored value
        item_type = self._proto_item_types.get(obj.pid)
        obj._proto_item_type = item_type

        if item_type == ItemType.WEAPON:
            obj.weapon_data = WeaponData(
                ammo_quantity=reader.read_int32(),
                ammo_type_pid=reader.read_int32()
            )
        elif item_type == ItemType.AMMO:
            obj.ammo_data = AmmoData(quantity=reader.read_int32())
        elif item_type == ItemType.MISC:
            obj.misc_item_data = MiscItemData(charges=reader.read_int32())
        elif item_type == ItemType.KEY:
            obj.key_data = KeyData(key_code=reader.read_int32())
        # ARMOR, CONTAINER, DRUG have no extra data

    def _read_scenery_data(self, reader: '_BinaryReader', obj: MapObject) -> None:
        """Read scenery-specific data based on scenery subtype."""
        scenery_type = self._proto_scenery_types.get(obj.pid)
        obj._proto_scenery_type = scenery_type

        if scenery_type == SceneryType.DOOR:
            obj.door_data = DoorData(open_flags=reader.read_int32())
        elif scenery_type == SceneryType.STAIRS:
            obj.stairs_data = StairsData(
                destination_map=reader.read_int32(),
                destination_built_tile=reader.read_int32()
            )
        elif scenery_type == SceneryType.ELEVATOR:
            obj.elevator_data = ElevatorData(
                type=reader.read_int32(),
                level=reader.read_int32()
            )
        elif scenery_type in (SceneryType.LADDER_UP, SceneryType.LADDER_DOWN):
            obj.ladder_data = LadderData(
                destination_built_tile=reader.read_int32()
            )
        # GENERIC has no extra data

    def _read_misc_data(self, reader: '_BinaryReader', obj: MapObject) -> None:
        """Read misc object data (exit grids)."""
        # Exit grids have PIDs 0x5000010 to 0x5000017
        if 0x5000010 <= obj.pid <= 0x5000017:
            obj.exit_grid_data = ExitGridData(
                map=reader.read_int32(),
                tile=reader.read_int32(),
                elevation=reader.read_int32(),
                rotation=reader.read_int32()
            )

    def parse_from_dat(self, dat: 'DATArchive', path: str) -> Map:
        """
        Parse a map from a DAT archive.

        Uses the proto types configured on this parser instance.

        Args:
            dat: Open DATArchive
            path: Path to map file within archive (e.g., 'MAPS\\JUNKENT.MAP')

        Returns:
            Parsed Map object

        Raises:
            FileNotFoundError: If map file not found in archive
        """
        content = dat.read_file(path)
        if content is None:
            raise FileNotFoundError(f"Map not found in archive: {path}")

        return self.parse(content)

    @staticmethod
    def list_maps(dat: 'DATArchive') -> List[str]:
        """
        List all map files in a DAT archive.

        Args:
            dat: Open DATArchive

        Returns:
            List of map file paths
        """
        return dat.list_files('*.MAP')


# =============================================================================
# Helper Classes
# =============================================================================

class _BinaryReader:
    """Helper class for reading binary data."""

    def __init__(self, data: bytes, offset: int = 0):
        self._data = data
        self._offset = offset

    @property
    def offset(self) -> int:
        return self._offset

    def skip(self, count: int) -> None:
        self._offset += count

    def read_bytes(self, count: int) -> bytes:
        result = self._data[self._offset:self._offset + count]
        self._offset += count
        return result

    def read_int32(self) -> int:
        """Read big-endian signed 32-bit integer."""
        result = struct.unpack('>i', self._data[self._offset:self._offset + 4])[0]
        self._offset += 4
        return result

    def read_uint32(self) -> int:
        """Read big-endian unsigned 32-bit integer."""
        result = struct.unpack('>I', self._data[self._offset:self._offset + 4])[0]
        self._offset += 4
        return result


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI interface for the map parser."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description='Fallout 1 Map File Parser',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all maps in archive
  python -m fallout_data.map /path/to/MASTER.DAT --list

  # Parse a map and show summary
  python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map

  # Show all critters on a map
  python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --critters

  # Show all objects with scripts
  python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --scripted

  # Show all scripts on a map
  python -m fallout_data.map /path/to/MASTER.DAT maps/junktown.map --scripts
"""
    )
    parser.add_argument('dat_file', help='Path to MASTER.DAT')
    parser.add_argument('map_path', nargs='?', help='Path to map within DAT (e.g., maps/junktown.map)')
    parser.add_argument('-l', '--list', action='store_true', help='List all maps in archive')
    parser.add_argument('-c', '--critters', action='store_true', help='Show only critters')
    parser.add_argument('-i', '--items', action='store_true', help='Show only items')
    parser.add_argument('-s', '--scripted', action='store_true', help='Show only objects with scripts')
    parser.add_argument('-S', '--scripts', action='store_true', help='Show scripts instead of objects')
    parser.add_argument('-e', '--elevation', type=int, help='Filter by elevation (0-2)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show detailed object/script info')

    args = parser.parse_args()

    from .dat import DATArchive

    try:
        with DATArchive(args.dat_file) as dat:
            if args.list:
                maps = MapParser.list_maps(dat)
                print(f"Found {len(maps)} maps in {args.dat_file}:")
                for map_path in maps:
                    print(f"  {map_path}")
                return

            if not args.map_path:
                parser.error("map_path is required unless using --list")

            # Parse the map
            map_parser = MapParser()
            map_data = map_parser.parse_from_dat(dat, args.map_path)

            print(f"Map: {map_data.header.name}")
            print(f"Version: {map_data.header.version}")
            print(f"Entry: tile={map_data.header.entering_tile}, "
                  f"elevation={map_data.header.entering_elevation}, "
                  f"rotation={map_data.header.entering_rotation}")
            print(f"Total objects: {len(map_data.objects)}")
            print(f"Total scripts: {len(map_data.scripts)}")
            print()

            # Show scripts mode
            if args.scripts:
                for script_type in range(SCRIPT_TYPE_COUNT):
                    type_scripts = map_data.scripts_by_type.get(script_type, [])
                    if not type_scripts:
                        continue

                    type_name = ScriptType(script_type).name
                    print(f"=== {type_name} Scripts ({len(type_scripts)}) ===")

                    for script in type_scripts:
                        if args.verbose:
                            _print_script_verbose(script)
                        else:
                            _print_script_brief(script)

                    print()
                return

            # Filter objects
            objects = map_data.objects

            if args.elevation is not None:
                objects = [o for o in objects if o.elevation == args.elevation]

            if args.critters:
                objects = [o for o in objects if o.is_critter]
            elif args.items:
                objects = [o for o in objects if o.is_item]

            if args.scripted:
                objects = [o for o in objects if o.has_script]

            # Group by elevation
            by_elev = {0: [], 1: [], 2: []}
            for obj in objects:
                by_elev[obj.elevation].append(obj)

            for elev in range(3):
                elev_objects = by_elev[elev]
                if not elev_objects:
                    continue

                print(f"=== Elevation {elev} ({len(elev_objects)} objects) ===")

                for obj in elev_objects:
                    if args.verbose:
                        _print_object_verbose(obj, map_data)
                    else:
                        _print_object_brief(obj)

                print()

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _print_script_brief(script: MapScript) -> None:
    """Print brief script info."""
    type_name = script.script_type.name if script.script_type else "UNKNOWN"
    oid_info = f" oid={script.scr_oid}" if script.scr_oid >= 0 else ""
    tile_info = ""
    if script.is_spatial:
        tile_info = f" tile={script.tile} elev={script.elevation} radius={script.radius}"
    print(f"  {type_name:8} idx={script.scr_script_idx:3}{oid_info}{tile_info}")


def _print_script_verbose(script: MapScript) -> None:
    """Print detailed script info."""
    print(f"  {script}")
    print(f"    scr_id=0x{script.scr_id:08X}, flags=0x{script.scr_flags:08X}")
    if script.is_spatial:
        print(f"    Spatial: tile={script.tile}, elev={script.elevation}, radius={script.radius}")
    elif script.is_timed:
        print(f"    Timed: time={script.time}")
    if script.scr_oid >= 0:
        print(f"    Owner object ID: {script.scr_oid}")
    if script.scr_num_local_vars > 0:
        print(f"    Local vars: offset={script.scr_local_var_offset}, count={script.scr_num_local_vars}")
    if script.fixed_param != 0:
        print(f"    Fixed param: {script.fixed_param}")
    print()


def _print_object_brief(obj: MapObject) -> None:
    """Print brief object info."""
    type_name = obj.object_type.name
    script_info = f" [script={obj.script_id_number}]" if obj.has_script else ""
    print(f"  {type_name:8} PID=0x{obj.pid:08X} tile={obj.tile:5}{script_info}")


def _print_object_verbose(obj: MapObject, map_data: Optional[Map] = None) -> None:
    """Print detailed object info."""
    print(f"  {obj}")
    print(f"    FID=0x{obj.fid:08X}, flags=0x{obj.flags:08X}")
    print(f"    Position: tile={obj.tile}, x={obj.x}, y={obj.y}")

    if obj.has_script:
        print(f"    Script: type={obj.script_type}, index={obj.script_id_number}")
        # Try to find the script details
        if map_data:
            script = map_data.get_script_for_object(obj)
            if script:
                print(f"    Script details: idx={script.scr_script_idx}, "
                      f"flags=0x{script.scr_flags:08X}, param={script.fixed_param}")

    if obj.critter_data:
        cd = obj.critter_data
        print(f"    Critter: HP={cd.hp}, radiation={cd.radiation}, poison={cd.poison}")
        print(f"    Combat: AP={cd.combat.ap}, team={cd.combat.team}, AI={cd.combat.ai_packet}")

    if obj.inventory:
        print(f"    Inventory: {len(obj.inventory)} items")
        for inv_item in obj.inventory[:5]:  # Show first 5
            print(f"      {inv_item.quantity}x PID=0x{inv_item.item.pid:08X}")

    if obj.door_data:
        print(f"    Door: locked={obj.door_data.is_locked}, jammed={obj.door_data.is_jammed}")

    if obj.stairs_data:
        sd = obj.stairs_data
        print(f"    Stairs: map={sd.destination_map}, tile={sd.destination_tile}, "
              f"elev={sd.destination_elevation}")

    if obj.exit_grid_data:
        eg = obj.exit_grid_data
        print(f"    Exit Grid: map={eg.map}, tile={eg.tile}, elev={eg.elevation}")

    print()


if __name__ == '__main__':
    main()

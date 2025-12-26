"""
Fallout 1 Script Bytecode Parser.

Parses compiled .INT script files from Fallout 1. Scripts are compiled
from Fallout's custom scripting language into bytecode executed by the
game's virtual machine.

File Structure:
- Bytes 0-41: Bytecode (code section, may extend past 42)
- Byte 42+: Procedure table header
  - 4 bytes: procedure count (big-endian)
  - 24 bytes per procedure (Procedure struct)
- After procedures: Identifiers table
  - 4 bytes: total size of identifiers
  - Null-terminated strings
- After identifiers: Static strings table
  - 4 bytes: total size
  - Null-terminated strings

Bytecode Format:
- 2-byte big-endian opcodes
- Opcode high byte determines type:
  - 0x80: VM opcode (NOOP, PUSH, JUMP, etc.)
  - 0xC0: Integer value follows (4 bytes)
  - 0xA0: Float value follows (4 bytes)
  - 0x90: Static string reference follows (4 bytes offset)
  - 0x98: Dynamic string reference follows (4 bytes offset)
"""

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import BinaryIO, Dict, Iterator, List, Optional, Tuple, Union

__all__ = [
    'Opcode', 'ValueType', 'ProcedureFlags',
    'Procedure', 'Instruction', 'Script', 'ScriptIterator'
]


class Opcode(IntEnum):
    """VM opcodes for Fallout's script interpreter."""
    NOOP = 0x8000
    PUSH = 0x8001
    ENTER_CRITICAL_SECTION = 0x8002
    LEAVE_CRITICAL_SECTION = 0x8003
    JUMP = 0x8004
    CALL = 0x8005
    CALL_AT = 0x8006
    CALL_WHEN = 0x8007
    CALLSTART = 0x8008
    EXEC = 0x8009
    SPAWN = 0x800A
    FORK = 0x800B
    A_TO_D = 0x800C
    D_TO_A = 0x800D
    EXIT = 0x800E
    DETACH = 0x800F
    EXIT_PROGRAM = 0x8010
    STOP_PROGRAM = 0x8011
    FETCH_GLOBAL = 0x8012
    STORE_GLOBAL = 0x8013
    FETCH_EXTERNAL = 0x8014
    STORE_EXTERNAL = 0x8015
    EXPORT_VARIABLE = 0x8016
    EXPORT_PROCEDURE = 0x8017
    SWAP = 0x8018
    SWAPA = 0x8019
    POP = 0x801A
    DUP = 0x801B
    POP_RETURN = 0x801C
    POP_EXIT = 0x801D
    POP_ADDRESS = 0x801E
    POP_FLAGS = 0x801F
    POP_FLAGS_RETURN = 0x8020
    POP_FLAGS_EXIT = 0x8021
    POP_FLAGS_RETURN_EXTERN = 0x8022
    POP_FLAGS_EXIT_EXTERN = 0x8023
    POP_FLAGS_RETURN_VAL_EXTERN = 0x8024
    POP_FLAGS_RETURN_VAL_EXIT = 0x8025
    POP_FLAGS_RETURN_VAL_EXIT_EXTERN = 0x8026
    CHECK_PROCEDURE_ARGUMENT_COUNT = 0x8027
    LOOKUP_PROCEDURE_BY_NAME = 0x8028
    POP_BASE = 0x8029
    POP_TO_BASE = 0x802A
    PUSH_BASE = 0x802B
    SET_GLOBAL = 0x802C
    FETCH_PROCEDURE_ADDRESS = 0x802D
    DUMP = 0x802E
    IF = 0x802F
    WHILE = 0x8030
    STORE = 0x8031
    FETCH = 0x8032
    EQUAL = 0x8033
    NOT_EQUAL = 0x8034
    LESS_THAN_EQUAL = 0x8035
    GREATER_THAN_EQUAL = 0x8036
    LESS_THAN = 0x8037
    GREATER_THAN = 0x8038
    ADD = 0x8039
    SUB = 0x803A
    MUL = 0x803B
    DIV = 0x803C
    MOD = 0x803D
    AND = 0x803E
    OR = 0x803F
    BITWISE_AND = 0x8040
    BITWISE_OR = 0x8041
    BITWISE_XOR = 0x8042
    BITWISE_NOT = 0x8043
    FLOOR = 0x8044
    NOT = 0x8045
    NEGATE = 0x8046
    WAIT = 0x8047
    CANCEL = 0x8048
    CANCEL_ALL = 0x8049
    START_CRITICAL = 0x804A
    END_CRITICAL = 0x804B

    # Dialog/say opcodes (from intlib.cc)
    SAYQUIT = 0x804C
    SAYEND = 0x804D
    SAYSTART = 0x804E
    SAYSTARTPOS = 0x804F
    SAYREPLYTITLE = 0x8050
    SAYGOTOREPLY = 0x8051
    SAYREPLY = 0x8052
    SAYOPTION = 0x8053
    SAYMESSAGE = 0x8054
    SAYREPLYWINDOW = 0x8055
    SAYOPTIONWINDOW = 0x8056
    SAYBORDER = 0x8057
    SAYSCROLLUP = 0x8058
    SAYSCROLLDOWN = 0x8059
    SAYSETSPACING = 0x805A
    SAYOPTIONCOLOR = 0x805B
    SAYREPLYCOLOR = 0x805C
    SAYRESTART = 0x805D
    SAYGETLASTPOS = 0x805E
    SAYREPLYFLAGS = 0x805F
    SAYOPTIONFLAGS = 0x8060
    SAYMESSAGETIMEOUT = 0x8061

    # Window opcodes
    CREATEWIN = 0x8062
    DELETEWIN = 0x8063
    SELECTWIN = 0x8064
    RESIZEWIN = 0x8065
    SCALEWIN = 0x8066
    SHOWWIN = 0x8067
    FILLWIN = 0x8068
    FILLRECT = 0x8069
    FILLWIN3X3 = 0x806A
    DISPLAY = 0x806B
    DISPLAYGFX = 0x806C
    DISPLAYRAW = 0x806D
    LOADPALETTETABLE = 0x806E
    FADEIN = 0x806F
    FADEOUT = 0x8070
    GOTOXY = 0x8071
    PRINT = 0x8072
    FORMAT = 0x8073
    PRINTRECT = 0x8074
    SETFONT = 0x8075
    SETTEXTFLAGS = 0x8076
    SETTEXTCOLOR = 0x8077
    SETHIGHLIGHTCOLOR = 0x8078
    STOPMOVIE = 0x8079
    PLAYMOVIE = 0x807A
    MOVIEFLAGS = 0x807B
    PLAYMOVIERECT = 0x807C

    # Region opcodes
    ADDREGION = 0x807F
    ADDREGIONFLAG = 0x8080
    ADDREGIONPROC = 0x8081
    ADDREGIONRIGHTPROC = 0x8082
    DELETEREGION = 0x8083
    ACTIVATEREGION = 0x8084
    CHECKREGION = 0x8085

    # Button opcodes
    ADDBUTTON = 0x8086
    ADDBUTTONTEXT = 0x8087
    ADDBUTTONFLAG = 0x8088
    ADDBUTTONGFX = 0x8089
    ADDBUTTONPROC = 0x808A
    ADDBUTTONRIGHTPROC = 0x808B
    DELETEBUTTON = 0x808C

    # Mouse opcodes
    HIDEMOUSE = 0x808D
    SHOWMOUSE = 0x808E
    MOUSESHAPE = 0x808F
    REFRESHMOUSE = 0x8090
    SETGLOBALMOUSEFUNC = 0x8091

    # Event opcodes
    ADDNAMEDEVENT = 0x8092
    ADDNAMEDHANDLER = 0x8093
    CLEARNAMED = 0x8094
    SIGNALNAMED = 0x8095
    ADDKEY = 0x8096
    DELETEKEY = 0x8097

    # Sound opcodes
    SOUNDPLAY = 0x8098
    SOUNDPAUSE = 0x8099
    SOUNDRESUME = 0x809A
    SOUNDSTOP = 0x809B
    SOUNDREWIND = 0x809C
    SOUNDDELETE = 0x809D

    # Misc opcodes
    SETONEOPTPAUSE = 0x809E
    SELECTFILELIST = 0x809F
    TOKENIZE = 0x80A0

    # Game-specific opcodes (from intextra.cc)
    GIVE_EXP_POINTS = 0x80A1
    SCR_RETURN = 0x80A2
    PLAY_SFX = 0x80A3
    OBJ_NAME = 0x80A4
    SFX_BUILD_OPEN_NAME = 0x80A5
    GET_PC_STAT = 0x80A6
    TILE_CONTAINS_PID_OBJ = 0x80A7
    SET_MAP_START = 0x80A8
    OVERRIDE_MAP_START = 0x80A9
    HAS_SKILL = 0x80AA
    USING_SKILL = 0x80AB
    ROLL_VS_SKILL = 0x80AC
    SKILL_CONTEST = 0x80AD
    DO_CHECK = 0x80AE
    IS_SUCCESS = 0x80AF
    IS_CRITICAL = 0x80B0
    HOW_MUCH = 0x80B1
    REACTION_ROLL = 0x80B2
    REACTION_INFLUENCE = 0x80B3
    RANDOM = 0x80B4
    ROLL_DICE = 0x80B5
    MOVE_TO = 0x80B6
    CREATE_OBJECT_SID = 0x80B7
    DISPLAY_MSG = 0x80B8
    SCRIPT_OVERRIDES = 0x80B9
    OBJ_IS_CARRYING_OBJ_PID = 0x80BA
    TILE_CONTAINS_OBJ_PID = 0x80BB
    SELF_OBJ = 0x80BC
    SOURCE_OBJ = 0x80BD
    TARGET_OBJ = 0x80BE
    DUDE_OBJ = 0x80BF
    OBJ_BEING_USED_WITH = 0x80C0
    LOCAL_VAR = 0x80C1
    SET_LOCAL_VAR = 0x80C2
    MAP_VAR = 0x80C3
    SET_MAP_VAR = 0x80C4
    GLOBAL_VAR = 0x80C5
    SET_GLOBAL_VAR = 0x80C6
    SCRIPT_ACTION = 0x80C7
    OBJ_TYPE = 0x80C8
    OBJ_ITEM_SUBTYPE = 0x80C9
    GET_CRITTER_STAT = 0x80CA
    SET_CRITTER_STAT = 0x80CB
    ANIMATE_STAND_OBJ = 0x80CC
    ANIMATE_STAND_REVERSE_OBJ = 0x80CD
    ANIMATE_MOVE_OBJ_TO_TILE = 0x80CE
    ANIMATE_JUMP = 0x80CF
    ATTACK = 0x80D0
    MAKE_DAYTIME = 0x80D1
    TILE_DISTANCE = 0x80D2
    TILE_DISTANCE_OBJS = 0x80D3
    TILE_NUM = 0x80D4
    TILE_NUM_IN_DIRECTION = 0x80D5
    PICKUP_OBJ = 0x80D6
    DROP_OBJ = 0x80D7
    ADD_OBJ_TO_INVEN = 0x80D8
    RM_OBJ_FROM_INVEN = 0x80D9
    WIELD_OBJ_CRITTER = 0x80DA
    USE_OBJ = 0x80DB
    OBJ_CAN_SEE_OBJ = 0x80DC
    ATTACK_COMPLEX = 0x80DD
    START_GDIALOG = 0x80DE
    END_DIALOGUE = 0x80DF
    DIALOGUE_REACTION = 0x80E0
    TURN_OFF_OBJS_IN_AREA = 0x80E1
    TURN_ON_OBJS_IN_AREA = 0x80E2
    SET_OBJ_VISIBILITY = 0x80E3
    LOAD_MAP = 0x80E4
    BARTER_OFFER = 0x80E5
    BARTER_ASKING = 0x80E6
    ANIM_BUSY = 0x80E7
    CRITTER_HEAL = 0x80E8
    SET_LIGHT_LEVEL = 0x80E9
    GAME_TIME = 0x80EA
    GAME_TIME_IN_SECONDS = 0x80EB
    ELEVATION = 0x80EC
    KILL_CRITTER = 0x80ED
    KILL_CRITTER_TYPE = 0x80EE
    CRITTER_DAMAGE = 0x80EF
    ADD_TIMER_EVENT = 0x80F0
    RM_TIMER_EVENT = 0x80F1
    GAME_TICKS = 0x80F2
    HAS_TRAIT = 0x80F3
    DESTROY_OBJECT = 0x80F4
    OBJ_CAN_HEAR_OBJ = 0x80F5
    GAME_TIME_HOUR = 0x80F6
    FIXED_PARAM = 0x80F7
    TILE_IS_VISIBLE = 0x80F8
    DIALOGUE_SYSTEM_ENTER = 0x80F9
    ACTION_BEING_USED = 0x80FA
    CRITTER_STATE = 0x80FB
    GAME_TIME_ADVANCE = 0x80FC
    RADIATION_INC = 0x80FD
    RADIATION_DEC = 0x80FE
    CRITTER_ATTEMPT_PLACEMENT = 0x80FF
    OBJ_PID = 0x8100
    CUR_MAP_INDEX = 0x8101
    CRITTER_ADD_TRAIT = 0x8102
    CRITTER_RM_TRAIT = 0x8103
    PROTO_DATA = 0x8104
    MESSAGE_STR = 0x8105
    CRITTER_INVEN_OBJ = 0x8106
    OBJ_SET_LIGHT_LEVEL = 0x8107
    WORLD_MAP = 0x8108
    TOWN_MAP = 0x8109
    FLOAT_MSG = 0x810A
    METARULE = 0x810B
    ANIM = 0x810C
    OBJ_CARRYING_PID_OBJ = 0x810D
    REG_ANIM_FUNC = 0x810E
    REG_ANIM_ANIMATE = 0x810F
    REG_ANIM_ANIMATE_REVERSE = 0x8110
    REG_ANIM_OBJ_MOVE_TO_OBJ = 0x8111
    REG_ANIM_OBJ_RUN_TO_OBJ = 0x8112
    REG_ANIM_OBJ_MOVE_TO_TILE = 0x8113
    REG_ANIM_OBJ_RUN_TO_TILE = 0x8114
    PLAY_GMOVIE = 0x8115
    ADD_MULT_OBJS_TO_INVEN = 0x8116
    RM_MULT_OBJS_FROM_INVEN = 0x8117
    GET_MONTH = 0x8118
    GET_DAY = 0x8119
    EXPLOSION = 0x811A
    DAYS_SINCE_VISITED = 0x811B
    GSAY_START = 0x811C
    GSAY_END = 0x811D
    GSAY_REPLY = 0x811E
    GSAY_OPTION = 0x811F
    GSAY_MESSAGE = 0x8120
    GIQ_OPTION = 0x8121
    POISON = 0x8122
    GET_POISON = 0x8123
    PARTY_ADD = 0x8124
    PARTY_REMOVE = 0x8125
    REG_ANIM_ANIMATE_FOREVER = 0x8126
    CRITTER_INJURE = 0x8127
    COMBAT_IS_INITIALIZED = 0x8128
    GDIALOG_BARTER = 0x8129
    DIFFICULTY_LEVEL = 0x812A
    RUNNING_BURNING_GUY = 0x812B
    INVEN_UNWIELD = 0x812C
    OBJ_IS_LOCKED = 0x812D
    OBJ_LOCK = 0x812E
    OBJ_UNLOCK = 0x812F
    OBJ_IS_OPEN = 0x8130
    OBJ_OPEN = 0x8131
    OBJ_CLOSE = 0x8132
    GAME_UI_DISABLE = 0x8133
    GAME_UI_ENABLE = 0x8134
    GAME_UI_IS_DISABLED = 0x8135
    GFADE_OUT = 0x8136
    GFADE_IN = 0x8137
    ITEM_CAPS_TOTAL = 0x8138
    ITEM_CAPS_ADJUST = 0x8139
    ANIM_ACTION_FRAME = 0x813A
    REG_ANIM_PLAY_SFX = 0x813B
    CRITTER_MOD_SKILL = 0x813C
    SFX_BUILD_CHAR_NAME = 0x813D
    SFX_BUILD_AMBIENT_NAME = 0x813E
    SFX_BUILD_INTERFACE_NAME = 0x813F
    SFX_BUILD_ITEM_NAME = 0x8140
    SFX_BUILD_WEAPON_NAME = 0x8141
    SFX_BUILD_SCENERY_NAME = 0x8142
    ATTACK_SETUP = 0x8143
    DESTROY_MULT_OBJS = 0x8144
    USE_OBJ_ON_OBJ = 0x8145
    ENDGAME_SLIDESHOW = 0x8146
    MOVE_OBJ_INVEN_TO_OBJ = 0x8147
    ENDGAME_MOVIE = 0x8148
    OBJ_ART_FID = 0x8149
    ART_ANIM = 0x814A
    PARTY_MEMBER_OBJ = 0x814B
    ROTATION_TO_TILE = 0x814C
    JAM_LOCK = 0x814D
    GDIALOG_SET_BARTER_MOD = 0x814E
    COMBAT_DIFFICULTY = 0x814F
    OBJ_ON_SCREEN = 0x8150
    CRITTER_IS_FLEEING = 0x8151
    CRITTER_SET_FLEE_STATE = 0x8152
    TERMINATE_COMBAT = 0x8153
    DEBUG_MSG = 0x8154
    CRITTER_STOP_ATTACKING = 0x8155


class ValueType(IntEnum):
    """Value type markers in opcodes."""
    OPCODE = 0x8000
    INT = 0xC001
    FLOAT = 0xA001
    STRING = 0x9001
    DYNAMIC_STRING = 0x9801
    PTR = 0xE001


class RawValueType(IntFlag):
    """Raw value type flags from the opcode high byte."""
    OPCODE = 0x8000
    INT = 0x4000
    FLOAT = 0x2000
    STATIC_STRING = 0x1000
    DYNAMIC_STRING = 0x0800


class ProcedureFlags(IntFlag):
    """Procedure attribute flags."""
    TIMED = 0x01
    CONDITIONAL = 0x02
    IMPORTED = 0x04
    EXPORTED = 0x08
    CRITICAL = 0x10


def _get_opcode_name(opcode: int) -> str:
    """Get opcode name from value (for external use)."""
    base_opcode = 0x8000 | (opcode & 0x3FF)
    try:
        return Opcode(base_opcode).name
    except ValueError:
        return f"UNKNOWN_{opcode:04X}"


@dataclass
class Procedure:
    """Represents a procedure/function in a script."""
    index: int
    name_offset: int
    flags: int
    time_value: int
    condition_address: int
    code_address: int
    arg_count: int
    name: str = ""

    @property
    def is_timed(self) -> bool:
        return bool(self.flags & ProcedureFlags.TIMED)

    @property
    def is_conditional(self) -> bool:
        return bool(self.flags & ProcedureFlags.CONDITIONAL)

    @property
    def is_imported(self) -> bool:
        return bool(self.flags & ProcedureFlags.IMPORTED)

    @property
    def is_exported(self) -> bool:
        return bool(self.flags & ProcedureFlags.EXPORTED)

    @property
    def is_critical(self) -> bool:
        return bool(self.flags & ProcedureFlags.CRITICAL)

    def __repr__(self) -> str:
        flags_str = []
        if self.is_timed:
            flags_str.append("timed")
        if self.is_conditional:
            flags_str.append("conditional")
        if self.is_imported:
            flags_str.append("imported")
        if self.is_exported:
            flags_str.append("exported")
        if self.is_critical:
            flags_str.append("critical")
        flags = ", ".join(flags_str) if flags_str else "none"
        return f"Procedure({self.name!r}, addr={self.code_address}, args={self.arg_count}, flags=[{flags}])"


@dataclass
class Instruction:
    """Represents a single bytecode instruction."""
    offset: int
    opcode: int
    operand: Optional[Union[int, float, str]] = None
    operand_type: Optional[str] = None
    size: int = 2  # Default instruction size (opcode only)

    @property
    def opcode_name(self) -> str:
        """Get human-readable opcode name."""
        # The base opcode is in the low 10 bits (& 0x3FF)
        # Plus the 0x8000 base for opcodes
        base_opcode = 0x8000 | (self.opcode & 0x3FF)
        try:
            return Opcode(base_opcode).name
        except ValueError:
            return f"UNKNOWN_{self.opcode:04X}"

    @property
    def is_push(self) -> bool:
        """True if this is a PUSH instruction (constant value)."""
        # PUSH is opcode 0x01, can appear as 0x8001, 0xC001, 0xA001, etc.
        return (self.opcode & 0x3FF) == 0x001

    @property
    def is_jump(self) -> bool:
        """True if this is a jump/branch instruction."""
        return self.opcode in (Opcode.JUMP, Opcode.IF, Opcode.WHILE)

    @property
    def is_call(self) -> bool:
        """True if this is a call instruction."""
        return self.opcode in (Opcode.CALL, Opcode.CALL_AT, Opcode.CALL_WHEN,
                               Opcode.CALLSTART, Opcode.EXEC, Opcode.SPAWN, Opcode.FORK)

    def __repr__(self) -> str:
        if self.operand is not None:
            if self.operand_type == 'string':
                return f"Instruction({self.offset:04X}: {self.opcode_name} {self.operand!r})"
            elif self.operand_type == 'float':
                return f"Instruction({self.offset:04X}: {self.opcode_name} {self.operand:.6f})"
            else:
                return f"Instruction({self.offset:04X}: {self.opcode_name} {self.operand})"
        return f"Instruction({self.offset:04X}: {self.opcode_name})"


class ScriptIterator:
    """
    Iterator for parsing Fallout script bytecode.

    Provides instruction-by-instruction iteration through script bytecode,
    parsing opcodes and their operands.

    Usage:
        script = Script.from_file(data)
        for instruction in script.iterate():
            print(instruction)

        # Or iterate from a specific offset
        iterator = ScriptIterator(script)
        iterator.seek(0x100)
        while iterator.has_more():
            instr = iterator.next()
            print(instr)
    """

    def __init__(self, script: 'Script', start_offset: int = 0):
        """
        Initialize iterator at a specific offset.

        Args:
            script: The Script object to iterate
            start_offset: Starting byte offset in bytecode
        """
        self._script = script
        self._offset = start_offset
        self._end_offset = script.code_end_offset

    @property
    def offset(self) -> int:
        """Current byte offset in the bytecode."""
        return self._offset

    @property
    def script(self) -> 'Script':
        """The script being iterated."""
        return self._script

    def seek(self, offset: int) -> None:
        """
        Move iterator to a specific offset.

        Args:
            offset: Byte offset to seek to
        """
        if offset < 0 or offset > self._end_offset:
            raise ValueError(f"Offset {offset} out of range [0, {self._end_offset}]")
        self._offset = offset

    def has_more(self) -> bool:
        """True if more instructions are available."""
        return self._offset + 2 <= self._end_offset

    def peek_opcode(self) -> Optional[int]:
        """
        Peek at the next opcode without advancing.

        Returns:
            Next opcode value, or None if at end
        """
        if not self.has_more():
            return None
        return self._script.read_word(self._offset)

    def next(self) -> Optional[Instruction]:
        """
        Parse and return the next instruction, advancing the iterator.

        Returns:
            Next Instruction, or None if at end
        """
        if not self.has_more():
            return None

        start_offset = self._offset
        opcode = self._script.read_word(self._offset)
        self._offset += 2

        instruction = Instruction(offset=start_offset, opcode=opcode)

        # Check if this is a PUSH instruction (opcode 0x01)
        # PUSH can appear as 0x8001 (bare), 0xC001 (int), 0xA001 (float), 0x9001 (string)
        base_opcode = opcode & 0x3FF
        high_byte = (opcode >> 8) & 0xFF

        if base_opcode == 0x001:  # PUSH opcode
            # PUSH is always followed by 4 bytes of data
            if self._offset + 4 <= self._end_offset:
                raw_value = self._script.read_long(self._offset)
                self._offset += 4
                instruction.size = 6

                # Determine type from high byte flags
                if high_byte & 0x40:  # INT flag (0xC0)
                    instruction.operand = self._to_signed32(raw_value)
                    instruction.operand_type = 'int'
                elif high_byte & 0x20:  # FLOAT flag (0xA0)
                    instruction.operand = struct.unpack('>f', struct.pack('>I', raw_value))[0]
                    instruction.operand_type = 'float'
                elif high_byte & 0x10:  # STATIC_STRING flag (0x90)
                    instruction.operand = self._script.get_static_string(raw_value)
                    instruction.operand_type = 'string'
                elif high_byte & 0x08:  # DYNAMIC_STRING flag (0x98)
                    instruction.operand = raw_value
                    instruction.operand_type = 'dynamic_string_offset'
                else:
                    # Bare PUSH (0x80) - treat as int
                    instruction.operand = self._to_signed32(raw_value)
                    instruction.operand_type = 'int'

        return instruction

    def _to_signed32(self, value: int) -> int:
        """Convert unsigned 32-bit value to signed."""
        if value >= 0x80000000:
            return value - 0x100000000
        return value

    def __iter__(self) -> Iterator[Instruction]:
        """Iterate through all instructions."""
        return self

    def __next__(self) -> Instruction:
        """Get next instruction or raise StopIteration."""
        instruction = self.next()
        if instruction is None:
            raise StopIteration
        return instruction


class Script:
    """
    Represents a parsed Fallout script (.INT file).

    Provides access to procedures, identifiers, static strings, and
    bytecode iteration.

    Usage:
        # Load from bytes
        script = Script.from_bytes(data)

        # Load from file in DAT archive
        with DATArchive('master.dat') as dat:
            script = Script.from_dat(dat, 'scripts/abel.int')

        # Access procedures
        for proc in script.procedures:
            print(proc.name, proc.code_address)

        # Iterate bytecode
        for instr in script.iterate():
            print(instr)

        # Iterate specific procedure
        for instr in script.iterate_procedure(script.procedures[0]):
            print(instr)
    """

    PROCEDURES_OFFSET = 42
    PROCEDURE_SIZE = 24

    def __init__(self, data: bytes, name: str = ""):
        """
        Initialize script from raw bytes.

        Args:
            data: Raw script file bytes
            name: Optional script name (for display)
        """
        self._data = data
        self.name = name
        self._procedures: List[Procedure] = []
        self._identifiers_offset = 0
        self._identifiers_size = 0
        self._static_strings_offset = 0
        self._static_strings_size = 0
        self._code_end = 0

        self._parse()

    def _parse(self) -> None:
        """Parse the script file structure."""
        if len(self._data) < self.PROCEDURES_OFFSET + 4:
            raise ValueError("Script data too small")

        # Parse procedure table
        proc_count = self.read_long(self.PROCEDURES_OFFSET)

        ptr = self.PROCEDURES_OFFSET + 4
        for i in range(proc_count):
            if ptr + self.PROCEDURE_SIZE > len(self._data):
                break

            name_offset = self.read_long(ptr)
            flags = self.read_long(ptr + 4)
            time_value = self.read_long(ptr + 8)
            condition_addr = self.read_long(ptr + 12)
            code_addr = self.read_long(ptr + 16)
            arg_count = self.read_long(ptr + 20)

            proc = Procedure(
                index=i,
                name_offset=name_offset,
                flags=flags,
                time_value=time_value,
                condition_address=condition_addr,
                code_address=code_addr,
                arg_count=arg_count
            )
            self._procedures.append(proc)
            ptr += self.PROCEDURE_SIZE

        # Identifiers table follows procedures
        self._identifiers_offset = ptr
        if ptr + 4 <= len(self._data):
            self._identifiers_size = self.read_long(ptr)

        # Static strings follow identifiers
        self._static_strings_offset = self._identifiers_offset + 4 + self._identifiers_size
        if self._static_strings_offset + 4 <= len(self._data):
            raw_size = self.read_long(self._static_strings_offset)
            # Handle potential invalid sizes (0xFFFFFFFF is common sentinel)
            if raw_size > len(self._data) or raw_size == 0xFFFFFFFF:
                self._static_strings_size = len(self._data) - self._static_strings_offset - 4
            else:
                self._static_strings_size = raw_size

        # Code extends through entire file (bytecode is before and after tables)
        # The initial bytes (0-41) are startup code, and main code follows after tables
        self._code_end = len(self._data)

        # Resolve procedure names
        for proc in self._procedures:
            proc.name = self.get_identifier(proc.name_offset)

    @classmethod
    def from_bytes(cls, data: bytes, name: str = "") -> 'Script':
        """
        Create Script from raw bytes.

        Args:
            data: Raw script file bytes
            name: Optional script name

        Returns:
            Parsed Script object
        """
        return cls(data, name)

    @classmethod
    def from_dat(cls, dat_archive, path: str) -> 'Script':
        """
        Load Script from DAT archive.

        Args:
            dat_archive: Open DATArchive instance
            path: Path to script within archive

        Returns:
            Parsed Script object

        Raises:
            FileNotFoundError: If script not found in archive
        """
        data = dat_archive.read_file(path)
        if data is None:
            raise FileNotFoundError(f"Script not found in archive: {path}")
        return cls(data, path)

    @property
    def procedures(self) -> List[Procedure]:
        """List of all procedures in the script."""
        return self._procedures

    @property
    def code_end_offset(self) -> int:
        """Offset where bytecode ends (start of procedure table)."""
        return self._code_end

    def read_word(self, offset: int) -> int:
        """Read big-endian 16-bit word at offset."""
        if offset + 2 > len(self._data):
            raise IndexError(f"Cannot read word at offset {offset}")
        return struct.unpack('>H', self._data[offset:offset+2])[0]

    def read_long(self, offset: int) -> int:
        """Read big-endian 32-bit long at offset."""
        if offset + 4 > len(self._data):
            raise IndexError(f"Cannot read long at offset {offset}")
        return struct.unpack('>I', self._data[offset:offset+4])[0]

    def get_identifier(self, offset: int) -> str:
        """
        Get identifier string at offset.

        Args:
            offset: Offset from identifiers base

        Returns:
            Null-terminated string at offset
        """
        if offset < 0:
            return ""

        # Offset is from the identifiers base (which includes the size field)
        abs_offset = self._identifiers_offset + offset
        if abs_offset >= len(self._data):
            return ""

        # Find null terminator
        end = abs_offset
        while end < len(self._data) and self._data[end] != 0:
            end += 1

        try:
            return self._data[abs_offset:end].decode('ascii', errors='replace')
        except:
            return ""

    def get_static_string(self, offset: int) -> str:
        """
        Get static string at offset.

        Args:
            offset: Offset from static strings base

        Returns:
            Null-terminated string at offset
        """
        if offset < 0:
            return ""

        # Static strings base is after the size field
        abs_offset = self._static_strings_offset + 4 + offset
        if abs_offset >= len(self._data):
            return ""

        # Find null terminator
        end = abs_offset
        while end < len(self._data) and self._data[end] != 0:
            end += 1

        try:
            return self._data[abs_offset:end].decode('ascii', errors='replace')
        except:
            return ""

    def iterate(self, start_offset: int = 0) -> ScriptIterator:
        """
        Create iterator for bytecode starting at offset.

        Args:
            start_offset: Starting byte offset (default 0)

        Returns:
            ScriptIterator positioned at start_offset
        """
        return ScriptIterator(self, start_offset)

    def iterate_procedure(self, proc: Procedure) -> ScriptIterator:
        """
        Create iterator for a specific procedure's bytecode.

        Args:
            proc: Procedure to iterate

        Returns:
            ScriptIterator positioned at procedure's code
        """
        return ScriptIterator(self, proc.code_address)

    def get_procedure(self, name: str) -> Optional[Procedure]:
        """
        Find procedure by name.

        Args:
            name: Procedure name (case-insensitive)

        Returns:
            Procedure if found, None otherwise
        """
        name_lower = name.lower()
        for proc in self._procedures:
            if proc.name.lower() == name_lower:
                return proc
        return None

    def disassemble(self, start: int = 0, end: Optional[int] = None) -> List[Instruction]:
        """
        Disassemble bytecode range to list of instructions.

        Args:
            start: Starting offset
            end: Ending offset (default: end of code)

        Returns:
            List of parsed Instructions
        """
        if end is None:
            end = self.code_end_offset

        instructions = []
        iterator = self.iterate(start)
        while iterator.offset < end and iterator.has_more():
            instr = iterator.next()
            if instr:
                instructions.append(instr)
                if iterator.offset > end:
                    break
        return instructions

    def disassemble_procedure(self, proc: Procedure) -> List[Instruction]:
        """
        Disassemble a procedure's bytecode.

        Note: This attempts to find the procedure's end by looking for
        return opcodes or the next procedure's start.

        Args:
            proc: Procedure to disassemble

        Returns:
            List of parsed Instructions
        """
        start = proc.code_address

        # Find end (next procedure's start or code end)
        end = self.code_end_offset
        for other in self._procedures:
            if other.code_address > start and other.code_address < end:
                end = other.code_address

        return self.disassemble(start, end)

    def __repr__(self) -> str:
        return f"Script({self.name!r}, {len(self._procedures)} procedures)"


def main():
    """CLI interface for the script bytecode parser."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description='Fallout 1 Script Bytecode Parser',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List procedures in a script
  python -m fallout_data.script /path/to/master.dat scripts/abel.int

  # Disassemble a specific procedure
  python -m fallout_data.script /path/to/master.dat scripts/abel.int -p start

  # Disassemble all procedures
  python -m fallout_data.script /path/to/master.dat scripts/abel.int --all

  # List all scripts in the archive
  python -m fallout_data.script /path/to/master.dat --list
"""
    )
    parser.add_argument('dat_file', help='Path to MASTER.DAT')
    parser.add_argument('script_path', nargs='?', help='Path to script within DAT (e.g., scripts/abel.int)')
    parser.add_argument('-p', '--procedure', help='Disassemble specific procedure by name')
    parser.add_argument('-a', '--all', action='store_true', help='Disassemble all procedures')
    parser.add_argument('-l', '--list', action='store_true', help='List all .INT scripts in the archive')
    parser.add_argument('-n', '--limit', type=int, default=100, help='Max instructions per procedure (default: 100)')

    args = parser.parse_args()

    # Import here to avoid circular imports
    from .dat import DATArchive

    try:
        with DATArchive(args.dat_file) as dat:
            if args.list:
                # List all scripts
                scripts = dat.list_files('*.INT')
                print(f"Found {len(scripts)} scripts in {args.dat_file}:")
                for script_path in scripts:
                    print(f"  {script_path}")
                return

            if not args.script_path:
                parser.error("script_path is required unless using --list")

            # Load the script
            script = Script.from_dat(dat, args.script_path)
            print(f"Script: {script.name}")
            print(f"Procedures: {len(script.procedures)}")
            print()

            if args.procedure:
                # Disassemble specific procedure
                proc = script.get_procedure(args.procedure)
                if not proc:
                    print(f"Procedure '{args.procedure}' not found.")
                    print("Available procedures:")
                    for p in script.procedures:
                        print(f"  {p.name}")
                    sys.exit(1)

                print(f"Procedure: {proc}")
                print("-" * 60)
                for instr in script.disassemble_procedure(proc)[:args.limit]:
                    print(f"  {instr}")

            elif args.all:
                # Disassemble all procedures
                for proc in script.procedures:
                    print(f"\n{'='*60}")
                    print(f"Procedure: {proc}")
                    print("-" * 60)
                    for instr in script.disassemble_procedure(proc)[:args.limit]:
                        print(f"  {instr}")

            else:
                # Just list procedures
                print("Procedures:")
                for proc in script.procedures:
                    print(f"  {proc}")
                print()
                print("Use -p <name> to disassemble a procedure, or -a for all.")

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

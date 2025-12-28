# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Fallout Community Edition is a fully working re-implementation of Fallout 1, maintaining the original gameplay while adding engine bugfixes and quality of life improvements. The codebase is a faithful reconstruction of the original Fallout engine (v1.1, November 1997) with modern platform support for Windows, macOS, Linux, Android, and iOS.

## Architecture

The codebase (~120,000 lines) is organized into three main layers:

### 1. Platform Layer (src/plib/)

Low-level abstractions for graphics, input, file I/O, and windowing.

**plib/gnw/** - Graphics/Window/Input subsystem
- `gnw.h`: Core window management system
- `input.h`: Keyboard and mouse input with SDL integration
- `svga.h`: Video/graphics rendering
- `button.h`: UI button system
- `text.h`: Text rendering
- `grbuf.h`: Graphics buffer management

**plib/db/** - Virtual filesystem
- `db.h`: File I/O abstraction for reading `.DAT` archives (master.dat, critter.dat)
- `lzss.h`: LZSS compression support
- Provides file API: `db_fopen()`, `db_fread()`, etc.

**plib/color/** - Palette management
**plib/assoc/** - Hash tables and associative arrays

### 2. Script Interpreter (src/int/)

Complete bytecode VM for Fallout's custom scripting language (342+ opcodes, stack-based execution).

- `intrpret.h`: Core interpreter with opcode execution
- `intlib.h`: Script library integration with game systems
- `export.h`: Exports native functions to scripts
- `window.h`: Script-accessible window system
- `dialog.h`: Dialog system for scripts
- `audio.h`, `audiof.h`, `sound.h`: Audio playback

The interpreter supports:
- Timed/conditional procedures
- Critical sections
- Coroutines (fork/spawn)
- Program detachment
- 5 script types: System, Spatial, Timed, Item, Critter
- 23 script procedures (START, TALK, COMBAT, DAMAGE, etc.)

### 3. Game Logic (src/game/)

Core Fallout gameplay systems (~88,600 lines).

**Object/Entity System:**
- `object.h`: Base object representation (262-line Object struct)
- `object_types.h`: Type definitions (Item, Critter, Scenery, Wall, Tile, Misc)
- `proto.h`: Prototype system (templates for objects)
- `protinst.h`: Prototype instantiation

**Map & World:**
- `map.h`: Map system with elevation, tiles, hex grid
- `tile.h`: Tile rendering for isometric view
- `worldmap.h`: Overworld travel system
- `automap.h`: Automapping functionality

**Combat:**
- `combat.h`: Turn-based combat engine
- `combatai.h`: AI behavior and decision-making
- `actions.h`: Action resolution and animations
- `anim.h`: 64 animation types for movement, combat, deaths, weapon actions

**RPG Systems:**
- `critter.h`: Character/NPC data and behavior
- `stat.h`: SPECIAL stat system
- `skill.h`: Skills system
- `perk.h`: Perks system
- `trait.h`: Character traits

**Scripting Integration:**
- `scripts.h`: Script management, hooks script procedures to game objects
- Provides game-script interface layer

**Content & Resources:**
- `art.h`: Art/sprite loading from `.FRM` files
- `cache.h`: Resource caching system
- `message.h`: Localized text via MessageList (enables internationalization)
- `gsound.h`: Game audio and music

**UI & Interface:**
- `intface.h`: Main game interface (640x100 status bar)
- `inventry.h`: Inventory UI
- `editor.h`: Character creation/editor
- `pipboy.h`: PipBoy interface

**Game Flow:**
- `main.h`: Main game loop entry point
- `game.h`: Game state management
- `loadsave.h`: Save/load serialization system
- `gmouse.cc`: Mouse / cursor game logic

**Tweaks System:**
- `tweaks.h`: Runtime configuration via `tweaks.ini`
- Supports quality-of-life enhancements like auto mouse mode and hover-to-hide roof

The player object is generally referred by `obj_dude`

### 4. Modern Platform Compatibility (src/ root)

- `audio_engine.h`: SDL audio wrapper with DirectSound-like API
- `fps_limiter.h`: Frame rate control
- `platform_compat.h`: OS-specific compatibility layer
- `movie_lib.h`: Video playback (`.MVE` format)

## Key Architectural Patterns

**Prototype-Instance Pattern:**
Objects are instances of prototypes (PIDs). Prototypes define templates with defaults; instances override as needed. Saves memory and enables data-driven design.

**Component-Based Objects:**
The `Object` struct uses unions to store type-specific data (ItemObjectData, CritterObjectData, SceneryObjectData), minimizing memory while supporting diverse types.

**Script-Driven Gameplay:**
Almost all game logic is script-driven. Scripts attach to objects via the Script struct and respond to 23 different procedure hooks.

**Database Abstraction:**
All game assets are accessed through the `db` layer, providing unified file API for both packed `.DAT` files and loose files.

**Message-Based Localization:**
All text strings go through the MessageList system for internationalization.

## Important Data Formats

**FID (File ID):** Object appearance, format: `(type << 24) | data`
**PID (Prototype ID):** Object template reference
**SID (Script ID):** Script identifier
**Built Tile:** Encodes tile, elevation, rotation in single integer

## Coordinate Systems

- Tile-based isometric grid
- 3 elevations per map
- Hex-based pathfinding

## Configuration Files

**fallout.cfg** - Main game configuration:
- `master_dat`, `critter_dat`: Paths to data archives
- `master_patches`, `critter_patches`: Patch file paths
- `music_path1`: Music directory (usually `data/sound/music/` or `sound/music/`)
- File names must match case-sensitive paths

**f1_res.ini** - Resolution and display settings:
```ini
[MAIN]
SCR_WIDTH=1280
SCR_HEIGHT=720
WINDOWED=1
```

**tweaks.ini** - Quality of life enhancements (loaded from base directory):
- Auto mouse mode switching
- Hover-to-hide roof

## Entry Point and Application Flow

**Entry:** `src/plib/gnw/winmain.cc` → `main()` → `gnw_main()` in `src/game/main.cc`

**Initialization:**
1. SDL and window system setup (GNW)
2. Game initialization (`game_init()`)
3. Load databases (master.dat, critter.dat)
4. Initialize subsystems (art, scripts, combat, etc.)
5. Play intro movies
6. Main menu → new/load game → `main_game_loop()`

**Main Loop:**
1. Process input events
2. Update game state
3. Execute scheduled scripts
4. Update animations
5. Render frame
6. Handle combat turns

## Code Organization

All code is in the `fallout` namespace (228 files).

**Persistence:** Full save/load system serializes object state, scripts, maps, inventory to DB_FILE streams.

**Memory Management:** Mix of custom allocators (gnw/memory.h) and standard C++ allocation.

## Development Notes

- When adding features, consider whether they should be backported from Fallout 2 (see README goals)
  - https://github.com/alexbatalov/fallout2-ce
- Maintain compatibility with original game data files (master.dat, critter.dat)

The game files can be found in `/Applications/Fallout/`, including MASTER.DAT
The game .app is located in `/Applications/Fallout/Fallout Community Edition.app`
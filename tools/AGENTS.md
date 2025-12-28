# AGENTS.md

Instructions for LLM tools working with the Python scripts in this directory.

## Python Parsing Logic

The Python parsing scripts in this directory are best-effort implementations for extracting data from Fallout's binary formats. **These scripts may contain bugs.** If you encounter missing data, incorrect values, or unexpected behavior, the problem is almost certainly a bug in the Python parsing code, not an issue with the source data.

## Source of Truth

The ultimate source of truth for how Fallout data formats work is the **C code in the main codebase** (`src/`). When something doesn't work correctly or data appears to be missing:

1. **Do not assume the data files are corrupted or malformed**
2. **Explore the C codebase** to understand how the original game reads and processes the data
3. Look at the relevant parsing functions in `src/plib/db/`, `src/game/proto.cc`, `src/game/scripts.cc`, and related files
4. Compare the Python implementation against the C implementation to find discrepancies

Be liberal in exploring the C codebase. The answer to parsing problems is in there.

## MASTER.DAT Is Not Corrupted

**Stop assuming MASTER.DAT could be corrupted. It is not.**

If your parsing returns incomplete, malformed, or missing data, the bug is in the parsing logic. The .DAT files shipped with Fallout are correct and complete.

## No Loose File Overrides

There are no scripts or files being overridden as loose files unless **explicitly stated**. Do not assume that missing data might exist as a loose file override somewhere. If the data appears missing, revisit the parsing logic.

## Debugging Approach

When data appears wrong or missing:

1. Check the C code to understand the correct format
2. Compare byte offsets and struct layouts between Python and C
3. Verify endianness handling
4. Check for off-by-one errors in size calculations
5. Look for conditional parsing paths that may be missed

The C code has been thoroughly tested against the actual game data. Trust it.

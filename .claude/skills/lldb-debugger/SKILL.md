---
name: lldb-debugger
description: Allows you to debug the game with LLDB step debugging and breakpoints utilizing pexpect
---

# LLDB Debugging with pexpect

Use the `mcp__pexpect__pexpect_tool` to interact with LLDB for debugging the Fallout executable.

## Quick Start

```python
# Spawn LLDB with the game executable
child = pexpect.spawn('lldb "/Applications/Fallout/Fallout Community Edition.app/Contents/MacOS/Fallout Community Edition"', encoding='utf-8', timeout=30)
child.expect(r'\(lldb\)')
```

## Key Patterns

### Setting Breakpoints
```python
child.sendline('breakpoint set -n function_name')
child.expect(r'\(lldb\)')
```

Note: Setting breakpoints triggers verbose symbol loading output. Expect large output that may need to be discarded.

### Running and Hitting Breakpoints
```python
child.sendline('run')
child.expect(r'stop reason', timeout=15)
```

### Stepping Through Code
```python
# Step over (next line)
child.sendline('n')
child.expect(r'stopped', timeout=10)

# Step into function
child.sendline('step')
child.expect(r'stopped', timeout=15)
```

### Inspecting State
```python
# View current frame with source context
child.sendline('frame info')
child.expect(r'\(lldb\)')

# View local variables
child.sendline('frame variable')
child.expect(r'\(lldb\)')

# Print specific variable
child.sendline('p variable_name')
child.expect(r'\(lldb\)')

# View backtrace
child.sendline('bt')
child.expect(r'\(lldb\)')
```

### Flushing Buffered Output
LLDB output can be fragmented. Use this pattern to flush:
```python
import time
time.sleep(0.3)
child.sendline('')
child.expect(r'\(lldb\)')
print(child.before)
```

### Clean Exit
```python
child.sendline('process kill')
child.expect(r'\(lldb\)')
child.sendline('quit')
child.expect(pexpect.EOF, timeout=5)
```

## Common Issues

### Large Symbol Loading Output
When setting breakpoints or stepping into new code, LLDB loads symbols which produces massive output. Use `child.before[-N:]` to get only the last N characters:
```python
print(child.before[-800:])
```

### Output Fragmentation
LLDB output may arrive in chunks. If output looks incomplete, send an empty line and expect the prompt again to flush the buffer.

### Timeouts on game_init
`game_init()` and similar initialization functions take several seconds. Use longer timeouts (15-30s) when stepping over these.

## Useful Breakpoint Locations

| Function | File | Purpose |
|----------|------|---------|
| `gnw_main` | main.cc:89 | Game entry point |
| `main_init_system` | main.cc:224 | System initialization |
| `game_init` | game.cc | Core game initialization |
| `main_game_loop` | main.cc | Main game loop |
| `map_load` | map.cc | Map loading |
| `combat_begin` | combat.cc | Combat start |

## Example Full Session

```python
# Start LLDB
child = pexpect.spawn('lldb "/Applications/Fallout/Fallout Community Edition.app/Contents/MacOS/Fallout Community Edition"', encoding='utf-8', timeout=30)
child.expect(r'\(lldb\)')

# Set breakpoint
child.sendline('breakpoint set -n gnw_main')
child.expect(r'\(lldb\)')

# Run to breakpoint
child.sendline('run')
child.expect(r'stop reason', timeout=15)

# Check where we are
child.sendline('frame info')
child.expect(r'\(lldb\)')
print(child.before[-800:])

# Inspect variables
child.sendline('frame variable')
child.expect(r'\(lldb\)')
import time; time.sleep(0.3)
child.sendline('')
child.expect(r'\(lldb\)')
print(child.before)

# Step and continue as needed...

# Exit
child.sendline('process kill')
child.expect(r'\(lldb\)')
child.sendline('quit')
child.expect(pexpect.EOF)
```

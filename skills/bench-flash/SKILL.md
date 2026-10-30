---
name: bench-flash
description: Flash a firmware image to a board on a lab bench safely, with bench locking, human approval and post-flash verification. Use whenever a task requires programming hardware.
compatibility: Requires bench-lock on PATH and access to the bench host. In CI, the job must run on the bench's runner with resource_group bench-<name>.
---
# Flashing a board

Never flash without human approval in an interactive session. In CI, flash only from a
job that runs on that bench's runner and holds `resource_group: bench-<name>`.

## Steps
1. Identify the bench and board: `list_devices(bench)` / `get_device(...)`.
2. `get_doc("bench/flash-procedure.md")` for the exact flash command and the verification
   method for this board.
3. Confirm the image: path, size, `sha256sum`, and which commit built it. If you can't
   name the commit, stop.
4. Ask for approval. Show bench, board, image hash and the exact command.
5. Run under the lock:
   ```text
   bench-lock <bench> --wait 600 -- <flash command from the doc>
   ```
   Exit 75 means the bench is busy. Report it. Don't retry in a loop.
6. Verify, using the method from the doc (readback compare, boot banner, version
   register). A flash isn't done until it's verified.

## Report
| Item | Value |
|------|-------|
| Bench / board | |
| Image sha256 | |
| Built from commit | |
| Command | |
| Exit status | |
| Verification | method + result |
| Approved by | |

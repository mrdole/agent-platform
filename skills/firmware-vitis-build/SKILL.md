---
name: firmware-vitis-build
description: Build firmware inside the vitis-dev container (Vitis 2024.2, Spartan-7 target) from an XSA exported elsewhere. Use when compiling, rebuilding the platform or BSP, or reproducing a CI build failure locally.
compatibility: Requires podman and the vitis-dev image. Image name, mounts and license settings are in knowledge doc tooling/vitis-container.md.
---
# Building in vitis-dev

Two facts that never change:
- The pipeline starts from an XSA/bitstream built elsewhere. Do not synthesize from RTL.
- Always build in the container, never with host tools, so results match CI.

## Steps
1. `get_doc("tooling/vitis-container.md")` for the image tag, mounts and license
   environment.
2. Find the build command in the repo's AGENTS.md. Don't invent one.
3. Run it in the container. The usual shape:
   ```text
   podman run --rm -v "$PWD":/work:Z -w /work <image:tag> <build command>
   ```
4. On failure, find the **first** error, not the last line. Classify it:
   - toolchain or container (missing tool, license, mount): report it. Don't work
     around it.
   - XSA mismatch (hardware platform differs from what the code expects): stop and
     report. Don't regenerate the XSA.
   - code: hand it back to the implementer with the error and file:line.

## Report
| Item | Value |
|------|-------|
| Image | `<name:tag>` |
| Command | `<exact command>` |
| Exit status | `<n>` |
| Warnings | `<count>` and any new ones |
| Artifacts | path, size, sha256 |

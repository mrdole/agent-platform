# Firmware profile rules

- Target: the Spartan-7 board. The build starts from an XSA/bitstream produced elsewhere.
  Never re-synthesize from RTL in this profile.
- Build only inside the vitis-dev container (skill `firmware-vitis-build`). Results from
  a host Vitis install don't count as evidence.
- Touch hardware only through `bench-lock` and the `bench-flash` skill. Flashing needs
  explicit human approval. The tester never flashes.
- Before review, the change must pass the repo's build command and unit tests from
  AGENTS.md. List both under Evidence.
- Keep changes inside the firmware repo. Changes to CI templates or the pipeline belong
  to the pipeline profile. List those under "Follow-ups".

# Bench profile rules

- Instruments are reached only through the `bench` MCP tools. Never open raw sockets,
  VISA sessions or serial ports from bash.
- Instrument addresses come from `list_devices` / `get_device`, never from memory or
  old logs.
- `query` is for reading (`?` commands). `write` stays off unless the operator enabled it
  for this session (`AP_BENCH_ALLOW_WRITE=1`), and even then only allowlisted commands
  pass.
- Never enable RF output or change output power, frequency or attenuation unless the task
  explicitly says so and a human approved it. See knowledge doc `bench/rf-safety.md`.
- Flash only through `bench-lock` (skill `bench-flash`). Exit code 75 means the bench is
  busy. Report it; do not loop.
- Every measurement in a report includes: requirement ID, limit, value, units,
  instrument IDN, timestamp.

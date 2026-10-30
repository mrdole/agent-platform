---
name: it-debug-triage
description: Read-only diagnosis of IT and infrastructure problems - connectivity, DNS, proxies, TLS and CA trust, services, disk, CI runners - on hosts the agent can reach. Use when asked why something is broken. Produces ranked findings and a recommended next step, not fixes.
---
# Triage

1. **Scope the symptom:** what fails, from where (host, network segment, user), since
   when, and what changed.
2. **Check known gotchas first:** `search_docs("<exact error text>")`, then the systems
   involved.
3. **Collect facts** with the profile's read-only commands. Common probes:
   | Question | Command |
   |----------|---------|
   | Name resolves? | `getent hosts <name>` / `dig <name>` |
   | Route / reachable? | `ip route`, `ping -c 3 <host>` |
   | TLS / CA trust? | `openssl s_client -connect <host>:443 -servername <host>` |
   | HTTP through proxy? | `curl -sI https://<host>/` (check `HTTPS_PROXY`/`NO_PROXY`) |
   | Service up? | `systemctl status <unit>`, `journalctl -u <unit> -n 50` |
   | Disk? | `df -h` |
4. **Rank hypotheses.** For each: evidence for, evidence against, and the one check that
   settles it.
5. **Recommend** an exact command or named runbook for a human, and the expected result.
6. If an issue was given and a human approves, post the findings:
   `glab issue note <iid> -m "<findings>"`. Redact hostnames or user data if the issue
   is widely visible.

Never print tokens, keys, `secrets.env`, or full `env` output.

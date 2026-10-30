---
name: meta-run-evals
description: Run the platform's golden-task evals against the real models, compare with the baseline, and report regressions. Use after changing prompts, skills, permissions or model mappings, and before proposing any self-improvement.
---
# Running evals

1. Choose scope: the profiles your change touches. A change to `core/` affects all of
   them.
2. Run:
   ```text
   python3 evals/run.py --profile <name> --baseline evals/baseline.json --out .eval-results
   ```
   (Omit `--profile` to run everything.) Exit 1 means a task that passed in the baseline
   now fails.
3. Read `.eval-results/<timestamp>/summary.md`. For any failure, open that task's
   `output.md` and `check.log` and find out why before claiming anything.
4. A change ships only if:
   - no regressions against the baseline, and
   - the targeted eval improves (or a new eval proves the new behavior).
5. Paste the summary table into the MR. Update `evals/baseline.json` only in an MR that
   says so in its title.

Model output varies between runs. If a result looks surprising, run the task again with
`--task <id>` before concluding anything.

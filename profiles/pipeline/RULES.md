# Pipeline profile rules

- Lint every CI config change with `glab ci lint` before handing it to review.
- Shared CI templates are consumed by other repos at pinned refs. A template change is a
  breaking change unless proven otherwise. Say which repos include it and at what ref.
- Do not bump image tags, template refs or tool versions unless that is the fix. If you
  do, explain why.
- Never read, print or change CI/CD variables, runner registration or protected settings.
  Report changes a maintainer must make under "Follow-ups".
- A code failure (test failing for a real reason) is not a pipeline problem. Report it
  and hand it to the right profile instead of making the test pass.

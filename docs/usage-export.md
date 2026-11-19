# Sharing anonymised usage (optional)

Partner programmes want to know which packs lead to real deployments. `genai-evals usage export` writes a summary a
partner can choose to send. It is **off by default, never sent by the tool, and contains no customer data**.

```bash
export GENAI_EVALS_USAGE_SALT=<a private, stable value>     # so the same project hashes the same way every month
genai-evals usage export out .genai-evals --out usage.json
genai-evals usage schema                                    # every field it can contain, and what it never contains
```

Read `usage.json` before you send it. Per run it holds: the month, mode (offline, online, Promptfoo import or
engine), pack and version, overlays, run profile, the number of cases as a range, targets, repeats, judges and how
many are calibrated, counts of passing, failing, invalid and skipped gates, each gate's outcome, adapters with
their validation status, provider types, and whether the run passed.

It never holds case ids, inputs, outputs, references, per-case scores, prompts, model ids, account or project ids,
URLs, file paths, run ids, git SHAs or suite names. Names the partner or customer wrote (a custom gate, pack,
overlay or profile) are replaced by a salted hash, so they can be counted but not read; the salt stays with the
partner.

**Agree it with the customer first.** Even aggregates say that a customer is evaluating a system, and roughly how
far along it is. The [SOW template](sow-template.md) has a line for it.

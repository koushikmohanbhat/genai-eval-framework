# Security

Please report vulnerabilities privately via GitHub Security Advisories on this repository (Security → Report a vulnerability).

## Design notes
- No secrets in configuration: inline API keys are rejected; use env vars, AWS Secrets Manager or GCP Secret Manager references.
- Online connectors are read-only; Terraform grants least-privilege roles and scopes metric writes to the `GenAIEvals` namespace.
- Gate rules are evaluated in a whitelisted AST sandbox (no attribute access, imports or comprehensions).
- Judge prompts, stored samples, reports and emitted spans contain tokenised text (`tokenize: true` by default).
- HTML reports escape all model output.
- `python` / `http` graders and targets execute code or call URLs you configure: treat suite files like code and review them.
- Supply chain: CodeQL, Dependabot, pip-audit, SBOM and build provenance on release.

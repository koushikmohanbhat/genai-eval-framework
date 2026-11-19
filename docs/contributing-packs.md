# Contributing a pack or overlay

Partner teams can ship domain overlays (healthcare, retail, BFSI, an ERP) as their own packages without touching the
core. This page is how to build one, the bar it should meet, and how to list it so other teams can find it.

## 1. Start from the template

```bash
genai-evals pack new retail-returns --extends tool-agent        # writes ./genai-evals-retail-returns/
```

You get a package with the entry point, an overlay with starter cases and gates, a README, and a GitHub Actions
workflow. Replace the starter cases with your domain's, keep the overlay's tag on every case, and write the
`description`. The workflow stays red on the contribution bar until you do.

## 2. Meet the contribution bar

```bash
genai-evals pack check genai_evals_retail_returns/retail_returns
```

| Check | Why |
|---|---|
| metadata: name, pack, a real description | so `pack list` and the index say what it is |
| every case carries one shared tag | so gates can address the overlay's slice |
| at least one critical case | the rules that must never fail |
| at least one security case (adversarial, injection, prompt leak, excessive agency) | attacks through the data your domain's system reads |
| no real-looking identifiers in the cases | **never ship customer data**; use synthetic values, and check none is a real person's |
| a gate on the overlay's slice | so the overlay can fail a run |
| passes on the pack's guarded offline system | the cases and references agree with the pack |
| fails on its vulnerable offline system | proof the overlay catches something |

Passing the bar means the overlay is well formed and catches the offline system's failures. It does not mean it
was run against a real system; say so in the index (`status`) when it has been.

Also: grader names must not clash with the pack's; prefix them with the overlay name. Add graders only for rules the
pack's own don't cover. Use PII detector sets (`pii: {detectors: [...]}`) for your domain's identifiers.

## 3. List it in the index

The index is `packs/_common/overlay-index.yaml`; `genai-evals pack list --available` shows it. Open a pull request
adding an entry:

```yaml
  - name: retail-returns
    extends: tool-agent
    title: "Retail returns: return windows, refunds to original payment, exchanges, fraud signals"
    verticals: [retail]
    owner: Acme Consulting (evals@acme.example)
    status: community            # example | community (passes pack check) | field-tested
    access: on-request           # public | on-request (private package)
    install: "pip install genai-evals-retail-returns --index-url <your index>"
    source: https://github.com/acme/genai-evals-retail-returns
    license: Apache-2.0          # or "proprietary"
    framework: ">=0.7.4"
```

A test checks every entry has the required fields and extends a real pack. Listing is not endorsement: the
maintainers check the entry, not the package. `field-tested` means the owner has run it in at least one engagement
(say where in general terms, never naming the customer).

Private overlays are fine: list them with `access: on-request` so other teams know they exist, or don't list them.

## Wanted

Open an issue to claim one: healthcare discharge-summary faithfulness (`rag-knowledge-assistant` with the
`us_hipaa` detectors), retail returns (`tool-agent`), BFSI KYC document extraction (`structured-extraction`),
insurance FNOL claims (`tool-agent`, against the connector hub's `insurance-core` fake), contact-centre QA.

## A template repository

`pack new` is the template. If your organisation wants a GitHub template repository, run `pack new` once, commit
the result to a repository and mark it as a template in its settings.

# acme-evals-sap: an example partner package

Shows how a partner ships domain IP for genai-evals **outside the open-source core**: a Python package that registers
an overlay with an entry point. Nothing in genai-evals changes, and the package can live in a private repository or
index.

```bash
pip install ./examples/partner-pack-sap
genai-evals pack list            # tool-agent ... (overlays: security-guardrails, sap-order-tools [partner])
genai-evals init my-sap-eval --pack tool-agent --overlay sap-order-tools
```

The `sap-order-tools` overlay adds ten SAP sales-order conversations to the tool-agent pack: status by SAP order
number, credit memos under and over the approval limit, ship-to changes before and after goods issue, a credit memo on
another sold-to party's order (OWASP LLM06), and instructions planted in order header and delivery texts (LLM01). It
runs offline on the pack's mock order system; see `overlay.yaml` for the live setup against the connector hub's SAP
profile.

To make your own: copy this folder, rename the package and the overlay, and register it in `pyproject.toml`:

```toml
[project.entry-points."genai_evals.overlays"]
my-overlay = "my_package:my_overlay_folder"      # the folder holds overlay.yaml, with `pack:` naming the pack
[project.entry-points."genai_evals.packs"]
my-pack = "my_package.my_pack"                   # a whole pack: a folder with pack.yaml
```

Without packaging, `--overlay ./path/to/folder` works too.

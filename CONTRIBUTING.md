# Contributing

1. `pip install -e ".[dev]"`
2. `ruff check src tests examples && pytest -q && python examples/insurance/demo.py`
3. New graders: subclass `Grader`, decorate with `@register("name")`, add tests and a row in `docs/graders.md`.
4. New sources: subclass `Source` with `kind` = spans | records | history | metrics | scores; always support `fixture:`;
   add a recorded fixture and a test; document it in `docs/sources.md` (including the AWS / GCP equivalent).
5. Keep live API calls behind optional extras and lazy imports.

FROM python:3.12-slim
RUN useradd --create-home evals
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[aws,gcp,openai,anthropic,otel]"
# Put your suites, prompts, datasets and sources.yaml in /evals (COPY in a derived image or mount at runtime).
COPY examples/insurance /evals
USER evals
WORKDIR /evals
ENTRYPOINT ["genai-evals"]
CMD ["--help"]

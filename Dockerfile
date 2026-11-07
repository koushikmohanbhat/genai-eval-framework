FROM python:3.12-slim
RUN useradd --create-home evals
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[aws,gcp,openai,anthropic,otel]"
# No suites or settings are baked in. Build a derived image that copies your own evals project into /evals
# (FROM <this image> / COPY evals/ /evals/), or mount it at runtime. Settings come from environment variables.
RUN mkdir -p /evals && chown evals /evals
USER evals
WORKDIR /evals
ENTRYPOINT ["genai-evals"]
CMD ["--help"]

"""genai-eval-framework: evals as code for GenAI apps and agents.

    from genai_evals import Runner, load_suite, load_providers
    result = Runner(load_suite("suite.yaml"), load_providers("providers.yaml")).run()
"""
__version__ = "0.1.0"

from .config import load_cases, load_providers, load_sources, load_suite  # noqa: E402
from .models import Case, Message, Sample, Score, ToolCall, Usage  # noqa: E402
from .prompts import PromptStore, load  # noqa: E402
from .runner import Runner, SuiteError  # noqa: E402

__all__ = ["__version__", "Runner", "SuiteError", "load_suite", "load_providers", "load_sources", "load_cases", "Case", "Sample", "Score",
           "Message", "ToolCall", "Usage", "PromptStore", "load"]

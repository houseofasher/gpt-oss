# gpt-oss Responses API server — Railway deployment image
#
# Builds a single, deterministic environment so runtime dependencies
# (uvicorn, fastapi, ...) are guaranteed to be importable. This avoids the
# Railway build-vs-runtime environment split that produced
# "ModuleNotFoundError: No module named 'uvicorn'".
#
# Defaults to the `remote` inference backend: the server forwards each prompt
# (Brain Orchestrator doctrines + retrieved knowledge included) to a real
# OpenAI-compatible LLM API and streams back a REAL answer. This is what makes
# answers genuine on a CPU-only host like Railway, which cannot run the gpt-oss
# weights itself.
#
# REQUIRED for real answers: set LLM_API_KEY on the server (Railway -> Variables).
# Optional: LLM_BASE_URL (default https://api.openai.com/v1 — any compatible API
# such as OpenRouter/Groq/Together/local vLLM), LLM_MODEL (default gpt-4o-mini),
# LLM_TEMPERATURE, LLM_MAX_TOKENS.
# Without a key, the server returns a clear "no model connected" message — never
# a fake answer. Set INFERENCE_BACKEND=stub only for offline smoke tests.

FROM python:3.12-slim

WORKDIR /app

# Copy the full source tree (the custom PEP 517 build backend lives in _build/
# and must be importable during `pip install .`).
COPY . .

# Pure-wheel install (GPTOSS_BUILD_METAL unset -> setuptools backend, no CMake).
RUN python -m pip install --upgrade pip \
    && python -m pip install .

# remote = CPU-only, calls a real LLM API (set LLM_API_KEY). Real answers.
ENV INFERENCE_BACKEND=remote
ENV PORT=8000

EXPOSE 8000

CMD ["python", "-m", "gpt_oss.responses_api.serve"]

"""Resolve credentials from env vars or cloud secret managers. Secrets never live in YAML."""
from __future__ import annotations

import os
from functools import lru_cache


def resolve_secret(auth: dict, key_env: str = "api_key_env", key_secret: str = "api_key_secret", default_env: str | None = None) -> str | None:
    if auth.get(key_env):
        val = os.environ.get(auth[key_env])
        if not val:
            raise RuntimeError(f"environment variable {auth[key_env]} is not set")
        return val
    if auth.get(key_secret):
        return _from_secret_manager(auth[key_secret])
    if default_env:
        return os.environ.get(default_env)
    return None


@lru_cache(maxsize=64)
def _from_secret_manager(ref: str) -> str:
    """aws-sm://<secret-id>[#json-key]  or  gcp-sm://projects/<p>/secrets/<s>/versions/<v>"""
    if ref.startswith("aws-sm://"):
        import json

        import boto3
        sid, _, field = ref.removeprefix("aws-sm://").partition("#")
        val = boto3.client("secretsmanager").get_secret_value(SecretId=sid)["SecretString"]
        return json.loads(val)[field] if field else val
    if ref.startswith("gcp-sm://"):
        from google.cloud import secretmanager  # type: ignore
        name = ref.removeprefix("gcp-sm://")
        if "/versions/" not in name:
            name += "/versions/latest"
        return secretmanager.SecretManagerServiceClient().access_secret_version(name=name).payload.data.decode()
    if ref.startswith("env://"):
        return os.environ[ref.removeprefix("env://")]
    raise ValueError(f"unsupported secret reference: {ref}")

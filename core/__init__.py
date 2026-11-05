"""paimind core — the engine shared by desktop, mobile and the HTTP API.

Nothing in this package imports PySide6, Flutter or FastAPI: it is pure Python so
the same decision logic runs on a phone (via the PWA/Flutter bridge), inside the
desktop app and behind the HTTP API.

Module map
----------
=======================  =======================================================
module                   responsibility
=======================  =======================================================
``config``               config.yaml loading, defaults, URL template helpers
``errors``               error codes, HTTP status map, ``AppError``
``schemas``              Pydantic v2 contract shared by all three front-ends
``hardware``             memory / CPU / AVX2 / disk / GPU detection
``tiers``                tier definitions, tri-state rules, recommendation
``engines``              Ollama / vLLM / llama-server / Docker probing
``inference``            backend abstraction + builtin/ollama/vllm/llamacpp
``prompt``               prompt construction and model-output parsing
``decision``             the pipeline (local -> online -> result)
``online``               trigger rules, 24 h cache, privacy switch
``search``               SerpAPI / Bing / Brave / DuckDuckGo providers
``download``             provider+mirror model, fallback, resume, SHA256
``hf``                   Hugging Face auth, repos, upload, set-as-source
``model_files``          on-disk inventory, checksums, custom import
``keys``                 keyring-backed secret storage
``usage``                local usage accounting
``runtime``              current tier / deploy mode persistence
``context``              shared collaborators for server and desktop
=======================  =======================================================
"""

from __future__ import annotations

__version__ = "0.0.1"
APP_NAME = "paimind"
DISPLAY_NAME = "应急食品"


def _install_system_certs() -> None:
    """Trust the OS certificate store (Windows Credential/Root store).

    Local proxies such as Watt Toolkit install their own root CA into the
    system store; Python's ``ssl`` module does not read that store by default,
    so HTTPS to the outside world fails with ``CERTIFICATE_VERIFY_FAILED``.
    ``truststore`` bridges the two.  Failure is non-fatal: the app still runs,
    only TLS to such proxies breaks.
    """

    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass


_install_system_certs()

"""Local Ollama client. Only loopback URLs are allowed, so text never leaves the machine."""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request

# Set to True only if you deliberately want to allow an Ollama server on your LAN.
ALLOW_NON_LOOPBACK = False

POLISH_STYLES = {
    "clean": (
        "Clean up",
        "You clean up dictated text. Fix grammar, punctuation and obvious transcription "
        "mistakes, and remove repetitions. Keep the speaker's meaning, tone and wording as "
        "close as possible. Do not add anything. Output only the corrected text.",
    ),
    "email": (
        "Email",
        "You turn dictated notes into a clear, polite, professional email body. Keep all "
        "facts, names, numbers and dates exactly. No subject line and no placeholders like "
        "[Name]. Output only the email text.",
    ),
    "bullets": (
        "Bullet points",
        "You turn dictated text into concise bullet points, one idea per bullet, starting "
        "each with '- '. Keep all facts, names, numbers and dates exactly. Output only the "
        "bullet list.",
    ),
}

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OllamaError(Exception):
    pass


def validate_base_url(url: str) -> str:
    """Returns the normalized URL or raises OllamaError if it isn't http on loopback."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "http" or not parsed.hostname:
        raise OllamaError("Use an http:// URL, e.g. http://127.0.0.1:11434")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OllamaError("The Ollama URL can't contain credentials, a query or a fragment")
    host = parsed.hostname
    if not ALLOW_NON_LOOPBACK:
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if not loopback:
            raise OllamaError("Ollama must run on this computer (localhost / 127.0.0.1)")
    port = parsed.port or 11434
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}"


def _request(base_url: str, path: str, body: dict | None = None, timeout: float = 5):
    url = validate_base_url(base_url) + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    # No proxies: a system proxy must never see local traffic.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise OllamaError(f"Ollama returned an error ({e.code})") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise OllamaError("Ollama isn't running. Start it and try again.") from e
    except ValueError as e:
        raise OllamaError("Unexpected reply from Ollama") from e


def list_models(base_url: str) -> list[str]:
    data = _request(base_url, "/api/tags")
    return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))


def pick_default_model(names: list[str]) -> str | None:
    chat = [n for n in names if "embed" not in n.lower()]
    for preferred in ("qwen2.5:7b", "qwen3.5:9b", "llama3.2:3b"):
        if preferred in chat:
            return preferred
    return chat[0] if chat else None


def generate(base_url: str, model: str, system: str, prompt: str, timeout: float = 120) -> str:
    data = _request(base_url, "/api/generate", {
        "model": model, "system": system, "prompt": prompt, "stream": False,
        "think": False, "options": {"temperature": 0.2},
    }, timeout=timeout)
    return _THINK.sub("", data.get("response", "")).strip()


def warm_up(base_url: str, model: str) -> None:
    """Load the model into memory (an empty prompt does just that), so the real request
    doesn't wait ~15 s for a cold start."""
    _request(base_url, "/api/generate", {"model": model, "prompt": "", "keep_alive": "10m"}, timeout=120)


def polish(base_url: str, model: str, style: str, text: str) -> str:
    _, system = POLISH_STYLES.get(style, POLISH_STYLES["clean"])
    result = generate(base_url, model, system, text)
    if not result:
        raise OllamaError("Ollama returned an empty answer")
    return result

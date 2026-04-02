# rag_pipeline/triton_client.py
"""Utilities for Triton Inference Server gRPC text generation."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List

import numpy as np
try:
    from transformers import AutoTokenizer
except ModuleNotFoundError:
    AutoTokenizer = None
try:
    from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput
except ModuleNotFoundError:
    InferenceServerClient = None
    InferInput = None
    InferRequestedOutput = None

from apps.platform.settings import (
    TRITON_URL,
    TOKENIZER_MAP,
    TEMPERATURE,
    TOP_P,
    CTX_SAFETY_MARGIN,
    DEFAULT_MAX_MODEL_LEN,
    MODEL_MAX_CONTEXT,
    TRITON_TIMEOUTS,
    get_model_max_output_tokens,
)
from apps.platform.settings import logger  # Shared pipeline logger.

# Shared Triton client for non-stream requests.
_triton_client: InferenceServerClient | None = None

# Lazy tokenizer cache.
_tokenizers: Dict[str, AutoTokenizer] = {}

# Prompt token-count cache (thread-safe LRU).
_PROMPT_TOKEN_CACHE: "OrderedDict[str, int]" = OrderedDict()
_PROMPT_TOKEN_CACHE_LOCK = threading.Lock()
_PROMPT_TOKEN_CACHE_MAX = 1024
_SHORT_PROMPT_CHAR_THRESHOLD = 2000
_SHORT_PROMPT_CHAR_TOKEN_RATIO = 4

# Marker used by gpt-oss style responses.
ASSISTANT_FINAL_MARKER = "assistantfinal"


def _require_transformers() -> Any:

    """Fail loudly only when tokenizer-backed runtime paths are actually used."""

    if AutoTokenizer is None:

        raise RuntimeError("transformers is required for NTIS Triton tokenizer runtime.")

    return AutoTokenizer


def _require_triton_grpc(name: str, value: Any) -> Any:

    """Fail loudly only when Triton gRPC-backed runtime paths are actually used."""

    if value is None:

        raise RuntimeError(f"tritonclient.grpc is required for NTIS Triton runtime: {name}")

    return value


# ---------------------------------------------------------------------------
# 0. gpt-oss helpers and tokenizer cache
# ---------------------------------------------------------------------------
def _is_gpt_oss_model(model_name: str) -> bool:
    """Return True when the model name refers to the gpt-oss family."""
    name = model_name.lower()
    return ("gpt" in name) and ("oss" in name)


def get_tokenizer_for_model(model_name: str) -> AutoTokenizer:
    """Load and cache the Hugging Face tokenizer for the model."""
    if model_name not in _tokenizers:
        tok_id = TOKENIZER_MAP[model_name]
        tokenizer_cls = _require_transformers()

        _tokenizers[model_name] = tokenizer_cls.from_pretrained(
            tok_id,
            trust_remote_code=True,
        )
    return _tokenizers[model_name]


def _prompt_cache_key(model_name: str, prompt: str) -> str:
    """Build a stable cache key for prompt token counting."""
    digest = hashlib.sha256()
    digest.update(model_name.encode("utf-8"))
    digest.update(b"|")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


def _get_cached_prompt_tokens(cache_key: str) -> int | None:
    """Read the prompt-token cache under the shared lock."""
    with _PROMPT_TOKEN_CACHE_LOCK:
        cached = _PROMPT_TOKEN_CACHE.get(cache_key)
        if cached is None:
            return None
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        return cached


def _set_cached_prompt_tokens(cache_key: str, token_count: int) -> None:
    """Write a prompt-token count into the LRU cache."""
    with _PROMPT_TOKEN_CACHE_LOCK:
        _PROMPT_TOKEN_CACHE[cache_key] = token_count
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        if len(_PROMPT_TOKEN_CACHE) > _PROMPT_TOKEN_CACHE_MAX:
            _PROMPT_TOKEN_CACHE.popitem(last=False)


def _get_prompt_tokens(model_name: str, prompt: str) -> int:
    """Estimate prompt tokens with a fast path and tokenizer fallback."""
    cache_key = _prompt_cache_key(model_name, prompt)
    cached = _get_cached_prompt_tokens(cache_key)
    if cached is not None:
        return cached

    if len(prompt) < _SHORT_PROMPT_CHAR_THRESHOLD:
        estimate = max(1, len(prompt) // _SHORT_PROMPT_CHAR_TOKEN_RATIO)
        _set_cached_prompt_tokens(cache_key, estimate)
        return estimate

    try:
        tok = get_tokenizer_for_model(model_name)
        # Keep special tokens out of the prompt budget estimate.
        ids = tok.encode(prompt, add_special_tokens=False)
        token_count = len(ids)
        _set_cached_prompt_tokens(cache_key, token_count)
        return token_count
    except Exception as e:
        logger.warning(f"[TRITON] prompt token count failed, using fallback estimate: {e}")
        # Never return a zero-token fallback estimate.
        fallback = max(1, len(prompt) // 2)
        _set_cached_prompt_tokens(cache_key, fallback)
        return fallback


# ---------------------------------------------------------------------------
# 1. max_new_tokens helpers
# ---------------------------------------------------------------------------
def _get_max_seq_len(model_name: str) -> int:
    """Resolve the model context window from settings or tokenizer metadata."""
    max_seq_len = MODEL_MAX_CONTEXT.get(model_name)
    if max_seq_len:
        return int(max_seq_len)

    try:
        tok = get_tokenizer_for_model(model_name)
        max_seq_len = getattr(tok, "model_max_length", DEFAULT_MAX_MODEL_LEN)
        # Ignore unrealistic Hugging Face sentinel values.
        if max_seq_len is None or max_seq_len > 100_000:
            return int(DEFAULT_MAX_MODEL_LEN)
        return int(max_seq_len)
    except Exception:
        return int(DEFAULT_MAX_MODEL_LEN)

def _compute_max_new_tokens(
        model_name: str,
        prompt: str,
        max_tokens_hint: int | None = None,
) -> int:
    """Compute a safe output budget from prompt size and model context."""
    prompt_tokens = _get_prompt_tokens(model_name, prompt)

    max_seq_len = MODEL_MAX_CONTEXT.get(model_name)
    if not max_seq_len:
        try:
            tok = get_tokenizer_for_model(model_name)
            max_seq_len = getattr(tok, "model_max_length", DEFAULT_MAX_MODEL_LEN)
            # Ignore unrealistic Hugging Face sentinel values.
            if max_seq_len is None or max_seq_len > 100_000:
                max_seq_len = DEFAULT_MAX_MODEL_LEN
        except Exception:
            max_seq_len = DEFAULT_MAX_MODEL_LEN

    MIN_NEW_TOKENS = 64      # Keep a minimum generation budget.

    # Respect an explicit max-token hint when one is provided.
    cap = (
        int(max_tokens_hint)
        if max_tokens_hint is not None
        else int(get_model_max_output_tokens(model_name))
    )

    available = max_seq_len - prompt_tokens - CTX_SAFETY_MARGIN
    if available <= 0:
        logger.warning(
            f"[TRITON] prompt exceeds safe context budget. "
            f"prompt_tokens={prompt_tokens}, max_seq_len={max_seq_len}"
        )
        # Return a bounded fallback even when the prompt is too large.
        return max(MIN_NEW_TOKENS, min(cap, 128))
    max_new = min(cap, available)
    return max(MIN_NEW_TOKENS, max_new)


# ---------------------------------------------------------------------------
# 2. gpt-oss assistantfinal helpers
# ---------------------------------------------------------------------------
def extract_final_answer(raw: str) -> str:
    """Extract the final answer segment from gpt-oss output."""
    if not raw:
        return ""

    text = str(raw).strip()
    idx = text.rfind(ASSISTANT_FINAL_MARKER)
    if idx == -1:
        # If the marker is missing, return the original text.
        return text

    final = text[idx + len(ASSISTANT_FINAL_MARKER):]
    # Trim marker-adjacent separators before returning.
    final = final.lstrip(" :\n\t")
    logger.info(final)
    return final.strip()


def stream_after_assistantfinal(chunks):
    """Yield only the text that appears after the assistantfinal marker."""
    marker = ASSISTANT_FINAL_MARKER.lower()
    seen = False
    buf = ""

    for chunk in chunks:
        if not chunk:
            continue

        buf += chunk

        if not seen:
            pos = buf.lower().find(marker)
            if pos == -1:
                # Keep buffering until the marker appears.
                continue

            # Start emitting once the marker has been seen.
            seen = True
            start = pos + len(ASSISTANT_FINAL_MARKER)
            # Drop the marker and leading separators.
            buf = buf[start:]
            buf = buf.lstrip(" :\n\t")

            if not buf:
                continue

        # Emit only the buffered answer segment.
        yield buf
        buf = ""

    # Flush any buffered tail content.
    if seen and buf:
        # Marker was found but buffered text remained at shutdown.
        yield buf
    elif not seen and buf:
        # Fallback for models that never emit the marker.
        # Emit the raw stream so the caller still gets output.
        logger.warning(
            "[gpt-oss] assistantfinal marker missing. Falling back to raw stream output."
        )
        yield buf


# ---------------------------------------------------------------------------
# 3. Triton client and input builders
def get_triton_client() -> InferenceServerClient:
    """Return the shared Triton client used for non-stream inference."""
    global _triton_client
    if _triton_client is None:
        try:
            client_cls = _require_triton_grpc("InferenceServerClient", InferenceServerClient)

            _triton_client = client_cls(url=TRITON_URL, verbose=False)
            logger.info(f"[TRITON] client connected to {TRITON_URL}")
        except Exception as e:
            logger.error(f"[TRITON] client connection failed: {e}")
            raise e
    return _triton_client


def _make_inputs(
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int | None = None,
):
    """Build Triton input tensors for text generation requests."""
    infer_input_cls = _require_triton_grpc("InferInput", InferInput)

    text = infer_input_cls("text_input", [1], "BYTES")
    text.set_data_from_numpy(
        np.array([prompt.encode("utf-8")], dtype=object)
    )

    # vLLM backend expects sampling parameters as JSON bytes.
    sparams = infer_input_cls("sampling_parameters", [1], "BYTES")
    # Serialize only sampling fields the backend understands.
    params = {
        "temperature": str(float(temperature)),
        "top_p": str(float(top_p)),
        "max_tokens": str(int(max_tokens)),
        # Streaming mode uses a dedicated BOOL tensor instead.
    }
    if top_k is not None:
        params["top_k"] = str(int(top_k))

    sparams.set_data_from_numpy(
        np.array([json.dumps(params).encode("utf-8")], dtype=object)
    )
    return text, sparams


# ---------------------------------------------------------------------------
# 4. Triton gRPC streaming helpers
# ---------------------------------------------------------------------------
def _create_stream_client() -> InferenceServerClient:
    """Create a dedicated Triton client for a single active stream."""
    client_cls = _require_triton_grpc("InferenceServerClient", InferenceServerClient)

    return client_cls(url=TRITON_URL, verbose=False)


def _resolve_stream_timeouts(
        model_name: str,
        request_type: str,
        first_token_timeout: int | None,
        idle_timeout: int | None,
) -> tuple[int, int]:
    """Resolve TTFT and idle timeouts for the request type."""
    fallback_first = 10 if first_token_timeout is None else int(first_token_timeout)
    fallback_idle = 20 if idle_timeout is None else int(idle_timeout)
    request_timeouts = TRITON_TIMEOUTS.get(model_name, {})
    model_timeouts = request_timeouts.get(request_type, None)
    if model_timeouts:
        model_first, model_idle = model_timeouts
        return (
            model_first if first_token_timeout is None else int(first_token_timeout),
            model_idle if idle_timeout is None else int(idle_timeout),
        )
    return fallback_first, fallback_idle


def _triton_stream_generator(
        model_name: str,
        prompt: str,
        text: InferInput,
        sparams: InferInput,
        first_token_timeout: int | None = 10,
        idle_timeout: int | None = 20,
        request_type: str = "stream",
):
    """Expose Triton streaming callbacks as a Python generator."""
    first_token_timeout, idle_timeout = _resolve_stream_timeouts(
        model_name,
        request_type,
        first_token_timeout,
        idle_timeout,
    )
    cli = _create_stream_client()  # Streams require their own client instance.

    infer_input_cls = _require_triton_grpc("InferInput", InferInput)

    stream_flag = infer_input_cls("stream", [1], "BOOL")
    stream_flag.set_data_from_numpy(np.array([True], dtype=bool))

    infer_requested_output_cls = _require_triton_grpc("InferRequestedOutput", InferRequestedOutput)

    outs = [infer_requested_output_cls("text_output")]

    q: List[str] = []
    done = threading.Event()

    def on_resp(result, error):
        """Push callback chunks into the local queue and watch for final signals."""
        if error:
            logger.error(f"[ERR] Triton Callback Error: {error}")
            done.set()
            return

        if result is None:
            done.set()
            return

        arr = result.as_numpy("text_output")
        if arr is not None and len(arr) > 0:
            raw = arr[0]
            chunk = raw.decode("utf-8", errors="ignore")
            q.append(chunk)

        is_final = False
        try:
            resp = result.get_response()
            params = getattr(resp, "parameters", None)
            if params:
                flag = params.get("triton_final_response")
                if flag and getattr(flag, "bool_param", False):
                    is_final = True
        except Exception as e:
            logger.debug(f"[STREAM] triton_final_response check failed: {e}")

        if is_final:
            done.set()
            return

    # Start the stream.
    cli.start_stream(callback=on_resp)
    cli.async_stream_infer(
        model_name,
        inputs=[text, sparams, stream_flag],
        outputs=outs,
    )

    try:
        start_time = time.time()
        last_yield_time = start_time
        got_first = False

        while not done.is_set() or q:
            if q:
                chunk = q.pop(0)
                got_first = True
                last_yield_time = time.time()
                yield chunk
            else:
                now = time.time()
                if not got_first and (now - start_time > first_token_timeout):
                    logger.warning("[WARN] First token timeout")
                    break
                if got_first and (now - last_yield_time > idle_timeout):
                    logger.warning("[WARN] Idle timeout after response started")
                    break
                time.sleep(0.005)
    finally:
        # Always stop the stream on exit.
        try:
            cli.stop_stream()
        except Exception as e:
            logger.warning(f"[STREAM] stop_stream failed: {e}")


# ---------------------------------------------------------------------------
# 5. Internal sync inference path
# ---------------------------------------------------------------------------
def _triton_infer_sync(
        model_name: str,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int | None = None,
) -> str:
    """Run the sync path by consuming the stream generator into one string."""
    text, sparams = _make_inputs(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
    )

    # Accumulate the streamed chunks into one response.
    accumulated_text = ""
    for chunk in _triton_stream_generator(
            model_name,
            prompt,
            text,
            sparams,
            first_token_timeout=10,
            idle_timeout=20,
            request_type="sync",
    ):
        accumulated_text += chunk

    accumulated_text = accumulated_text.strip()

    # Strip gpt-oss control text before returning.
    if _is_gpt_oss_model(model_name):
        return extract_final_answer(accumulated_text)

    return accumulated_text


# ---------------------------------------------------------------------------
# 6. Public Triton inference entrypoint
# ---------------------------------------------------------------------------
def log_prompt_tokens(model_name: str, prompt: str, tag: str = ""):
    """Log prompt token counts for context-budget triage."""
    try:
        tok = get_tokenizer_for_model(model_name)
        ids = tok.encode(prompt, add_special_tokens=False)
        logger.info(
            f"[TRITON][TOK] {tag} model={model_name}, "
            f"prompt_chars={len(prompt)}, prompt_tokens={len(ids)}"
        )
    except Exception as e:
        logger.warning(f"[TRITON][TOK] token count failed: {e}")

def triton_infer(
        model_name: str,
        prompt: str,
        *,
        stream: bool = True,
        max_tokens: int | None = None,
        temperature: float = TEMPERATURE,
        top_p: float = TOP_P,
        top_k: int | None = None,
        timeout_first: int | None = None,
        timeout_idle: int | None = None,
):
    """Public Triton inference wrapper for sync and streaming requests."""
    logger.info(f"[TRITON] infer start - model={model_name}, len={len(prompt)}")
    log_prompt_tokens(model_name, prompt, tag="infer")

    if max_tokens is None:
        max_tokens = int(get_model_max_output_tokens(model_name))

    dynamic_max_tokens = _compute_max_new_tokens(
        model_name=model_name,
        prompt=prompt,
        max_tokens_hint=max_tokens,
    )

    if stream:
        # Resolve stream-specific timeouts.
        timeout_first, timeout_idle = _resolve_stream_timeouts(
            model_name,
            "stream",
            timeout_first,
            timeout_idle,
        )
        text, sparams = _make_inputs(
            prompt,
            max_tokens=dynamic_max_tokens,
            temperature=float(TEMPERATURE if temperature is None else temperature),
            top_p=float(TOP_P if top_p is None else top_p),
            top_k=top_k,
        )

        base_gen = _triton_stream_generator(
            model_name,
            prompt,
            text,
            sparams,
            first_token_timeout=timeout_first,
            idle_timeout=timeout_idle,
            request_type="stream",
        )

        # Emit only the answer portion for gpt-oss streams.
        if _is_gpt_oss_model(model_name):
            logger.info("[TRITON] gpt-oss detected, filtering assistantfinal stream")
            return stream_after_assistantfinal(base_gen)

        # Non-gpt-oss models can stream raw chunks directly.
        return base_gen
    # Sync path consumes the same generator into a final string.
    return _triton_infer_sync(
        model_name,
        prompt,
        max_tokens=dynamic_max_tokens,
        temperature=float(TEMPERATURE if temperature is None else temperature),
        top_p=float(TOP_P if top_p is None else top_p),
        top_k=top_k,
    )



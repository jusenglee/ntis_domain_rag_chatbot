# rag_pipeline/triton_client.py
"""
Triton Inference Server gRPC 클라이언트 래퍼 모듈.

주요 기능:
- 모델별 토크나이저 캐싱 및 프롬프트 토큰 길이 계산
- max_new_tokens를 시퀀스 길이에 맞게 동적으로 계산
- 스트리밍/비스트리밍 공용 엔트리 포인트 triton_infer()

주의 사항:
- Triton Python gRPC 클라이언트는 "하나의 InferenceServerClient 인스턴스당
  동시 active stream은 1개"만 허용한다.
  → 이 모듈에서는 **스트리밍용은 매 호출마다 별도의 클라이언트 인스턴스**를 생성하고,
    싱글톤 클라이언트는 non-stream(관리용) API에만 사용한다.
"""

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from typing import Dict, List

import numpy as np
from transformers import AutoTokenizer
from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput

from settings import (
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
from settings import logger  # 공용 logger

# 싱글톤 Triton 클라이언트 (모델 관리, non-stream 호출용)
_triton_client: InferenceServerClient | None = None

# 모델별 토크나이저 캐시
_tokenizers: Dict[str, AutoTokenizer] = {}

# 프롬프트 토큰 길이 캐시 (해시 기반, LRU)
_PROMPT_TOKEN_CACHE: "OrderedDict[str, int]" = OrderedDict()
_PROMPT_TOKEN_CACHE_LOCK = threading.Lock()
_PROMPT_TOKEN_CACHE_MAX = 1024
_SHORT_PROMPT_CHAR_THRESHOLD = 2000
_SHORT_PROMPT_CHAR_TOKEN_RATIO = 4
_HARMONY_FINAL_MODEL = "gpt_oss_triton_0"
_HARMONY_FINAL_MARKER = "<|channel|>final<|message|>"
_HARMONY_END_MARKERS = ("<|return|>/<|end|>", "<|return|>", "<|end|>")

_HARMONY_FULL_PATTERN = re.compile(
    r"<\|start\|>assistant<\|channel\|>final<\|message\|>(.*?)<\|return\|>/<\|end\|>",
    flags=re.DOTALL,
)
_HARMONY_FALLBACK_PATTERN = re.compile(
    r"<\|channel\|>final<\|message\|>(.*)",
    flags=re.DOTALL,
)

# ---------------------------------------------------------------------------
# 0. 토크나이저 관련 유틸
# ---------------------------------------------------------------------------
def get_tokenizer_for_model(model_name: str) -> AutoTokenizer:
    """
    모델 이름에 대응하는 토크나이저를 캐시해서 반환.
    TOKENIZER_MAP[model_name] 에 실제 HF 모델/로컬 경로가 매핑되어 있다고 가정.
    """
    if model_name not in _tokenizers:
        tok_id = TOKENIZER_MAP[model_name]
        _tokenizers[model_name] = AutoTokenizer.from_pretrained(
            tok_id,
            trust_remote_code=True,
        )
    return _tokenizers[model_name]


def _prompt_cache_key(model_name: str, prompt: str) -> str:
    digest = hashlib.sha256()
    digest.update(model_name.encode("utf-8"))
    digest.update(b"|")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


def _get_cached_prompt_tokens(cache_key: str) -> int | None:
    with _PROMPT_TOKEN_CACHE_LOCK:
        cached = _PROMPT_TOKEN_CACHE.get(cache_key)
        if cached is None:
            return None
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        return cached


def _set_cached_prompt_tokens(cache_key: str, token_count: int) -> None:
    with _PROMPT_TOKEN_CACHE_LOCK:
        _PROMPT_TOKEN_CACHE[cache_key] = token_count
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        if len(_PROMPT_TOKEN_CACHE) > _PROMPT_TOKEN_CACHE_MAX:
            _PROMPT_TOKEN_CACHE.popitem(last=False)


def _get_prompt_tokens(model_name: str, prompt: str) -> int:
    """
    주어진 모델 기준으로 프롬프트 토큰 길이 계산.

    - 토크나이저가 없거나 문제가 생기면 len(prompt) 기반으로
      아주 러프하게 fallback 한다.
    """
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
        # special token은 시스템 프롬프트 등에 이미 포함되어 있을 수 있으니 False
        ids = tok.encode(prompt, add_special_tokens=False)
        token_count = len(ids)
        _set_cached_prompt_tokens(cache_key, token_count)
        return token_count
    except Exception as e:
        logger.warning(f"[TRITON] prompt token 계산 실패, fallback 사용: {e}")
        # 완전 비었으면 0 보다는 1 이상으로 반환
        fallback = max(1, len(prompt) // 2)
        _set_cached_prompt_tokens(cache_key, fallback)
        return fallback


def _should_apply_harmony_final(model_name: str) -> bool:
    return model_name == _HARMONY_FINAL_MODEL


def _trim_harmony_end_markers(text: str) -> str:
    trimmed = text
    for marker in _HARMONY_END_MARKERS:
        marker_idx = trimmed.find(marker)
        if marker_idx != -1:
            trimmed = trimmed[:marker_idx]
    return trimmed


def _extract_harmony_final(text: str) -> str:
    """
    Harmony 형식 응답에서 final 채널 내용만 추출.

    지원 포맷:
    1) <|start|>assistant<|channel|>final<|message|>...<|return|>/<|end|>
    2) <|channel|>final<|message|>...

    매칭 실패 시에는 원문을 반환한다.
    """
    full_match = _HARMONY_FULL_PATTERN.search(text)
    if full_match:
        return full_match.group(1).strip()

    fallback_match = _HARMONY_FALLBACK_PATTERN.search(text)
    if fallback_match:
        return _trim_harmony_end_markers(fallback_match.group(1)).strip()

    return text.strip()


def _extract_harmony_visible_stream_text(buffer: str) -> str:
    final_idx = buffer.find(_HARMONY_FINAL_MARKER)
    if final_idx != -1:
        content = buffer[final_idx + len(_HARMONY_FINAL_MARKER):]
        return _trim_harmony_end_markers(content)

    # Harmony 제어 토큰이 보이면 final 채널이 나오기 전까지는 숨긴다.
    if "<|channel|>" in buffer or "<|start|>" in buffer or "<|message|>" in buffer:
        return ""

    # plain text 응답은 기존처럼 그대로 노출
    return buffer


# ---------------------------------------------------------------------------
# 1. max_new_tokens 동적 계산
# ---------------------------------------------------------------------------
def _get_max_seq_len(model_name: str) -> int:
    max_seq_len = MODEL_MAX_CONTEXT.get(model_name)
    if max_seq_len:
        return int(max_seq_len)

    try:
        tok = get_tokenizer_for_model(model_name)
        max_seq_len = getattr(tok, "model_max_length", DEFAULT_MAX_MODEL_LEN)
        # HF 쪽에서 종종 엄청 큰 값(1e30 같은) 넣어두는 경우 방어
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
    """
    - 토크나이저의 model_max_length(없으면 8192 추정)를 기준으로
      prompt_tokens + max_new_tokens <= max_seq_len - margin 을 만족하도록 조정.
    - max_tokens_hint(= 호출자가 지정한 max_tokens)는 상한(cap)으로만 사용.

    반환:
        실제로 Triton에 넘길 max_new_tokens 값.
    """
    prompt_tokens = _get_prompt_tokens(model_name, prompt)

    max_seq_len = MODEL_MAX_CONTEXT.get(model_name)
    if not max_seq_len:
        try:
            tok = get_tokenizer_for_model(model_name)
            max_seq_len = getattr(tok, "model_max_length", DEFAULT_MAX_MODEL_LEN)
            # HF 쪽에서 종종 엄청 큰 값(1e30 같은) 넣어두는 경우 방어
            if max_seq_len is None or max_seq_len > 100_000:
                max_seq_len = DEFAULT_MAX_MODEL_LEN
        except Exception:
            max_seq_len = DEFAULT_MAX_MODEL_LEN

    MIN_NEW_TOKENS = 64      # 최소 생성 토큰

    # 모델별 기본 상한을 사용하고, 인자로 들어오면 그것으로 override
    cap = (
        int(max_tokens_hint)
        if max_tokens_hint is not None
        else int(get_model_max_output_tokens(model_name))
    )

    available = max_seq_len - prompt_tokens - CTX_SAFETY_MARGIN
    if available <= 0:
        logger.warning(
            f"[TRITON] prompt가 이미 max_seq_len을 거의 다 쓴 상태입니다: "
            f"prompt_tokens={prompt_tokens}, max_seq_len={max_seq_len}"
        )
        # 그래도 최소한 조금은 생성하도록
        return max(MIN_NEW_TOKENS, min(cap, 128))

    max_new = min(cap, available)
    return max(MIN_NEW_TOKENS, max_new)


# ---------------------------------------------------------------------------
# 3. Triton 클라이언트 생성/재사용
# ---------------------------------------------------------------------------
def get_triton_client() -> InferenceServerClient:
    """
    Triton Client Singleton.

    - 모델 상태 조회, load/unload 등 관리용/단일 요청용에 사용.
    - 스트리밍은 InferenceServerClient의 제약(동시 1스트림) 때문에
      별도의 인스턴스를 사용한다.
    """
    global _triton_client
    if _triton_client is None:
        try:
            _triton_client = InferenceServerClient(url=TRITON_URL, verbose=False)
            logger.info(f"✅ Triton Client connected to {TRITON_URL}")
        except Exception as e:
            logger.error(f"❌ Triton Client connection failed: {e}")
            raise e
    return _triton_client


def _make_inputs(
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
):
    """
    Triton vLLM backend에 맞는 입력 텐서를 구성.

    - text_input: BYTES, shape [1]
    - sampling_parameters: BYTES, shape [1], 내부는 JSON 문자열
    """
    text = InferInput("text_input", [1], "BYTES")
    text.set_data_from_numpy(
        np.array([prompt.encode("utf-8")], dtype=object)
    )

    # vLLM-backend 권장 명칭: sampling_parameters
    sparams = InferInput("sampling_parameters", [1], "BYTES")
    # vLLM Python backend 쪽 구현이 문자열 기반 파싱을 사용하는 경우가 많음
    params = {
        "temperature": str(float(temperature)),
        "top_p": str(float(top_p)),
        "max_tokens": str(int(max_tokens)),
        # stream 플래그는 별도 BOOL 인풋("stream")으로 전달
    }

    sparams.set_data_from_numpy(
        np.array([json.dumps(params).encode("utf-8")], dtype=object)
    )
    return text, sparams


# ---------------------------------------------------------------------------
# 4. 스트리밍 제너레이터 (Triton gRPC streaming)
# ---------------------------------------------------------------------------
def _create_stream_client() -> InferenceServerClient:
    """
    스트리밍 전용 Triton 클라이언트 생성.

    - Triton Python gRPC 클라이언트는 한 인스턴스당 동시 active stream 1개만 허용.
    - 동시 다중 스트리밍(예: decide_rag_needed + 본문의 스트림 응답)을 위해
      매 호출마다 별도의 InferenceServerClient를 생성해서 사용한다.
    """
    return InferenceServerClient(url=TRITON_URL, verbose=False)


def _resolve_stream_timeouts(
        model_name: str,
        request_type: str,
        first_token_timeout: int | None,
        idle_timeout: int | None,
) -> tuple[int, int]:
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
    """
    Triton gRPC streaming 호출을 래핑한 제너레이터.

    - 내부적으로 start_stream / async_stream_infer / stop_stream 을 관리
    - 콜백에서 들어오는 text_output을 큐에 쌓았다가 순차적으로 yield
    - first_token_timeout: 첫 토큰이 올 때까지의 최대 대기시간
    - idle_timeout: 응답이 시작된 이후 추가 토큰이 오지 않을 경우 타임아웃
    """
    first_token_timeout, idle_timeout = _resolve_stream_timeouts(
        model_name,
        request_type,
        first_token_timeout,
        idle_timeout,
    )
    cli = _create_stream_client()  # ⚠️ 스트리밍용으로 별도 클라이언트 생성

    stream_flag = InferInput("stream", [1], "BOOL")
    stream_flag.set_data_from_numpy(np.array([True], dtype=bool))

    outs = [InferRequestedOutput("text_output")]

    q: List[str] = []
    done = threading.Event()
    apply_harmony_final = _should_apply_harmony_final(model_name)

    def on_resp(result, error):
        """
        Triton 스트림 콜백:
        - text_output을 UTF-8 문자열로 디코드해서 큐에 쌓음
        - triton_final_response 파라미터를 보고 최종 응답 여부를 판단
        """
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

    # 스트림 시작
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

        harmony_buffer = ""
        flushed_len = 0

        while not done.is_set() or q:
            if q:
                chunk = q.pop(0)
                got_first = True
                last_yield_time = time.time()

                if not apply_harmony_final:
                    yield chunk
                    continue

                harmony_buffer += chunk
                visible_text = _extract_harmony_visible_stream_text(harmony_buffer)
                if len(visible_text) > flushed_len:
                    delta = visible_text[flushed_len:]
                    flushed_len = len(visible_text)
                    if delta:
                        yield delta
            else:
                now = time.time()
                if not got_first and (now - start_time > first_token_timeout):
                    logger.warning("[WARN] First token timeout")
                    break
                if got_first and (now - last_yield_time > idle_timeout):
                    logger.warning("[WARN] Idle timeout after response started")
                    break
                time.sleep(0.005)

        if apply_harmony_final:
            final_text = _extract_harmony_final(harmony_buffer)
            if len(final_text) > flushed_len:
                delta = final_text[flushed_len:]
                if delta:
                    yield delta
    finally:
        # 스트림 종료 (에러/정상 여부와 상관없이)
        try:
            cli.stop_stream()
        except Exception as e:
            logger.warning(f"[STREAM] stop_stream failed: {e}")


# ---------------------------------------------------------------------------
# 5. 동기(internal) infer (스트리밍 → accumulate)
# ---------------------------------------------------------------------------
def _triton_infer_sync(
        model_name: str,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
) -> str:
    """
    내부용 sync infer.

    - 호출 측에서 미리 _compute_max_new_tokens 로 계산된 max_tokens를 받아 사용.
    - 실제로는 스트리밍을 사용하되, 모든 chunk 를 모아서 하나의 문자열로 반환.
    """
    # 1) Triton 입력 텐서 구성
    text, sparams = _make_inputs(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

    # 2) 스트리밍 → 전체 문자열 accumulate
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

    if _should_apply_harmony_final(model_name):
        accumulated_text = _extract_harmony_final(accumulated_text)

    return accumulated_text


# ---------------------------------------------------------------------------
# 6. 공용 엔트리 포인트: triton_infer()
# ---------------------------------------------------------------------------
def log_prompt_tokens(model_name: str, prompt: str, tag: str = ""):
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
        timeout_first: int | None = None,
        timeout_idle: int | None = None,
):
    """
    Triton vLLM backend 공용 infer 함수.

    인자:
        - model_name: Triton 상의 모델 이름
        - prompt    : 입력 프롬프트 문자열
        - stream    : True → 제너레이터 반환, False → 최종 문자열 반환
        - max_tokens: 생성 토큰 상한 (동기 모드에서는 동적으로 조정됨)
        - temperature, top_p: 샘플링 파라미터
        - timeout_first: 스트리밍 첫 토큰 타임아웃
        - timeout_idle : 스트리밍 idle 타임아웃

    반환:
        - stream=True  → 제너레이터 (yield str)
        - stream=False → str (전체 응답)
    """
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
        # 스트리밍 모드
        timeout_first, timeout_idle = _resolve_stream_timeouts(
            model_name,
            "stream",
            timeout_first,
            timeout_idle,
        )
        text, sparams = _make_inputs(
            prompt,
            max_tokens=dynamic_max_tokens,
            temperature=temperature,
            top_p=top_p,
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

        # 모델별 후처리 반영 스트림 반환
        return base_gen

    # Sync Path: 스트리밍을 내부적으로 사용하여 최종 문자열 반환
    return _triton_infer_sync(
        model_name,
        prompt,
        max_tokens=dynamic_max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

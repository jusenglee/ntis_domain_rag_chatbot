# rag_pipeline/triton_client.py
"""\nTriton Inference Server gRPC 클라이언트 래퍼 모듈.\n\n주요 기능:\n- 모델별 토크나이저 캐싱 및 프롬프트 토큰 길이 계산\n- max_new_tokens를 시퀀스 길이에 맞게 동적으로 계산\n- 스트리밍/비스트리밍 공용 엔트리 포인트 triton_infer()\n\n주의 사항:\n- Triton Python gRPC 클라이언트는 "하나의 InferenceServerClient 인스턴스당\n  동시 active stream은 1개"만 허용한다.\n  → 이 모듈에서는 **스트리밍용은 매 호출마다 별도의 클라이언트 인스턴스**를 생성하고,\n    싱글톤 클라이언트는 non-stream(관리용) API에만 사용한다.\n"""

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Dict, List

import numpy as np
from transformers import AutoTokenizer
from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput

from apps.core.settings import (
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
from apps.core.settings import logger  # 공용 logger

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

# gpt-oss 계열이 최종 답변 앞에 붙이는 마커
ASSISTANT_FINAL_MARKER = "assistantfinal"


# ---------------------------------------------------------------------------
# 0. gpt-oss 판별 / 토크나이저 관련 유틸
# ---------------------------------------------------------------------------
def _is_gpt_oss_model(model_name: str) -> bool:
    """모델 이름이 gpt-oss 계열인지 단순 패턴으로 판정한다.
    최종 답변 마커 처리를 적용할지 결정하는 초기 gate로 쓴다.
    """
    name = model_name.lower()
    return ("gpt" in name) and ("oss" in name)


def get_tokenizer_for_model(model_name: str) -> AutoTokenizer:
    """모델별 Hugging Face tokenizer를 lazy cache로 가져온다.
    프롬프트 토큰 수 계산은 빈번하므로 같은 모델에 대한 tokenizer 초기화 비용을 이 함수에서 흡수한다.
    """
    if model_name not in _tokenizers:
        tok_id = TOKENIZER_MAP[model_name]
        _tokenizers[model_name] = AutoTokenizer.from_pretrained(
            tok_id,
            trust_remote_code=True,
        )
    return _tokenizers[model_name]


def _prompt_cache_key(model_name: str, prompt: str) -> str:
    """모델과 prompt 조합을 LRU cache 키로 안정적게 정규화한다.
    프롬프트 전문을 직접 키로 쓰지 않고 sha256 digest로 줄여 메모리 사용량을 억제한다.
    """
    digest = hashlib.sha256()
    digest.update(model_name.encode("utf-8"))
    digest.update(b"|")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


def _get_cached_prompt_tokens(cache_key: str) -> int | None:
    """프롬프트 토큰 캐시에서 값을 읽으며 LRU 순서를 갱신한다.
    스레드 경합을 피하기 위해 lock 안에서만 cache를 조작한다.
    """
    with _PROMPT_TOKEN_CACHE_LOCK:
        cached = _PROMPT_TOKEN_CACHE.get(cache_key)
        if cached is None:
            return None
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        return cached


def _set_cached_prompt_tokens(cache_key: str, token_count: int) -> None:
    """계산된 토큰 수를 prompt cache에 기록하고 LRU 크기를 유지한다.
    상한을 넘으면 가장 오래된 항목을 버려 토큰 캐시가 무제한 성장하지 않게 한다.
    """
    with _PROMPT_TOKEN_CACHE_LOCK:
        _PROMPT_TOKEN_CACHE[cache_key] = token_count
        _PROMPT_TOKEN_CACHE.move_to_end(cache_key)
        if len(_PROMPT_TOKEN_CACHE) > _PROMPT_TOKEN_CACHE_MAX:
            _PROMPT_TOKEN_CACHE.popitem(last=False)


def _get_prompt_tokens(model_name: str, prompt: str) -> int:
    """프롬프트 길이를 토큰 수로 환산하고 실패 시 fallback 추정치를 사용한다.
    짧은 프롬프트는 문자 수 비례 추정으로 처리하고, 긴 프롬프트는 실제 tokenizer를 사용해 max_new_tokens 계산 정확도를 지킨다.
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


# ---------------------------------------------------------------------------
# 1. max_new_tokens 동적 계산
# ---------------------------------------------------------------------------
def _get_max_seq_len(model_name: str) -> int:
    """모델이 실제로 소화할 수 있는 최대 context length를 얻는다.
    설정 오버라이드, tokenizer metadata, default fallback 순서로 안전한 상한을 고른다.
    """
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
    """프롬프트 길이와 모델 context 상한을 고려해 생성 토큰 상한을 계산한다.
    프롬프트가 기니 max context를 거의 다 쓰더라도 완전히 0으로 만들지 않고 최소 생성 분량을 남겨 서비스 응답을 보장한다.
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
# 2. gpt-oss assistantfinal 포맷 처리
# ---------------------------------------------------------------------------
def extract_final_answer(raw: str) -> str:
    """gpt-oss 응답 텍스트에서 `assistantfinal` 뒤의 최종 답변만 추출한다.
    마커가 없으면 원문을 유지하고, 있으면 앞쪽 control text를 버려 사용자 가시 답변만 남긴다.
    """
    if not raw:
        return ""

    text = str(raw).strip()
    idx = text.rfind(ASSISTANT_FINAL_MARKER)
    if idx == -1:
        # 마커 없으면 그냥 원본 반환
        return text

    final = text[idx + len(ASSISTANT_FINAL_MARKER):]
    # 콜론/공백 정리
    final = final.lstrip(" :\n\t")
    logger.info(final)
    return final.strip()


def stream_after_assistantfinal(chunks):
    """stream chunk 흐름에서 `assistantfinal` 마커를 찾은 뒤부터만 외부로 흘려보낸다.
    마커를 보기 전까지는 버퍼링하고, 마커 이후는 최종 답변 스트림으로 간주한다.
    """
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
                # 아직 마커 안 나왔으면 계속 버퍼에만 쌓음
                continue

            # 처음으로 마커를 발견한 시점
            seen = True
            start = pos + len(ASSISTANT_FINAL_MARKER)
            # 마커 앞부분은 버리고, 마커 뒤부터 사용
            buf = buf[start:]
            buf = buf.lstrip(" :\n\t")

            if not buf:
                continue

        # 여기부터는 전부 '최종 답변'에 해당
        yield buf
        buf = ""

    # 스트림 종료 후 마무리 처리
    if seen and buf:
        # assistantfinal 이후 남은 찌꺼기
        yield buf
    elif not seen and buf:
        # assistantfinal이 한 번도 안 나온 경우 fallback:
        # 전체 버퍼를 그냥 보내거나, 정책에 따라 버릴 수도 있음.
        logger.warning(
            "[gpt-oss] assistantfinal 마커를 찾지 못했습니다. 전체 버퍼를 그대로 전송합니다."
        )
        yield buf


# ---------------------------------------------------------------------------
# 3. Triton 클라이언트 생성/재사용
# ---------------------------------------------------------------------------
def get_triton_client() -> InferenceServerClient:
    """non-stream 호출과 관리용 API에 재사용할 singleton Triton client를 돌려준다.
    스트리밍은 별도 client를 생성하므로, 이 함수의 결과를 concurrent stream에 쓰면 안 된다.
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
    """Triton text-generation model이 기대하는 numpy input tensor 세트를 조립한다.
    프롬프트, 생성 상한, 샘플링 파라미터를 Triton gRPC 형식으로 직렬화한다.
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
    """스트리밍 호출용 일회성 Triton client를 생성한다.
    한 client당 active stream 하나만 허용하는 Triton 제약 때문에 요청마다 새 인스턴스를 만든다.
    """
    return InferenceServerClient(url=TRITON_URL, verbose=False)


def _resolve_stream_timeouts(
        model_name: str,
        request_type: str,
        first_token_timeout: int | None,
        idle_timeout: int | None,
) -> tuple[int, int]:
    """스트리밍 세션에 적용할 TTFT·생성·전체 데드라인을 설정에서 해석한다.
    호출자가 재정의한 타임아웃이 있으면 그 값을 우선하고, 없으면 모델별 default로 돌아간다.
    """
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
    """Triton 스트리밍 callback을 Python generator로 바꿈 상위 레이어가 순차 소비하게 한다.
    콜백에서 들어온 chunk, 오류, timeout 상태를 하나의 흐름으로 변환해 SSE 호출과 로그 계측이 같은 계약을 쓰게 한다.
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

    def on_resp(result, error):
        """Triton stream callback에서 응답 chunk나 예외를 queue에 적재한다.
        생성자 스레드와 callback 스레드 사이의 경계를 이 queue가 맞추며, 종료 sentinel도 같이 넣는다.
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
    # 1) Triton 입력 텐서 구성
    """non-stream Triton inference를 실행하고 텍스트 응답을 반환한다.
    출력 tensor 형식이 bytes이든 string이든 같은 문자열로 복원해 상위 파이프라인이 모델 종류 차이를 의식하지 않게 한다.
    """
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

    # 3) gpt-oss 계열이면 assistantfinal 이후만 추출
    if _is_gpt_oss_model(model_name):
        return extract_final_answer(accumulated_text)

    return accumulated_text


# ---------------------------------------------------------------------------
# 6. 공용 엔트리 포인트: triton_infer()
# ---------------------------------------------------------------------------
def log_prompt_tokens(model_name: str, prompt: str, tag: str = ""):
    """현재 prompt에 대한 토큰 추정치와 생성 상한을 로그로 남긴다.
    모델이 context limit에 가깝주 돌아서는지 triage할 때 보는 관측 포인트다.
    """
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
    """스트리밍과 비스트리밍 Triton 호출을 통합하는 최상위 엔트리포인트다.
    모델별 max_new_tokens 계산, gpt-oss final answer 정리, timeout 계측, stream/filter 후처리를 한 곳에서 맞춘다.
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

        # gpt-oss 계열이면 assistantfinal 이후만 스트리밍
        if _is_gpt_oss_model(model_name):
            logger.info("[TRITON] gpt-oss 모델 감지 → assistantfinal 이후만 스트리밍")
            return stream_after_assistantfinal(base_gen)

        # 그 외 모델은 raw 스트림 그대로
        return base_gen

    # Sync Path: 스트리밍을 내부적으로 사용하여 최종 문자열 반환
    return _triton_infer_sync(
        model_name,
        prompt,
        max_tokens=dynamic_max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

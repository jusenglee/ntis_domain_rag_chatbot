# rag_pipeline/settings.py
from __future__ import annotations

"""
시스템 전역 설정 및 하이퍼파라미터 관리 모듈입니다.
이 모듈은 환경 변수를 읽어 시스템의 동작 방식을 결정하며, 특히 RAG(Retrieval-Augmented Generation)의 
성능과 품질에 직결되는 각종 임계값(Threshold)과 파라미터를 중앙 집중식으로 관리합니다.

핵심 설정 영역:
1. 인프라 접속: Qdrant, Triton, Redis 등 외부 서비스 연결 정보
2. 모델 경로: 로컬에 저장된 임베딩 및 LLM 토크나이저 경로
3. RAG 성능 파라미터: 검색 후보군 개수, 유사도 점수, 토큰 예산 등
4. 타임아웃 및 재시도: 모델 호출 시의 안정성 확보를 위한 시간 제한 설정
"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass

import sys
from loguru import logger

# 로깅 설정: 표준 logging 라이브러리의 출력을 Loguru로 통합하여 일관된 로그 형식을 유지합니다.
class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def setup_logging():
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO",
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

setup_logging()


def _get_env_str(name: str, default: str) -> str:
    """환경변수를 문자열로 읽고 공백을 정리한다.
    빈 값은 default로 돌린 뒤 일관된 trim된 문자열로 정책 계산에 넘긴다.
    """
    return str(os.getenv(name, default) or "").strip()


def _get_env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    """정수형 환경변수를 읽고 필요하면 하한선을 검사한다.
    설정 값이 잘못되었을 때 조용히 넘기지 않고 즉시 예외를 내어 부트 시점에 잘못된 env를 드러낸다.
    """
    raw = _get_env_str(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}")
    return value


def _get_env_float(name: str, default: float, *, min_value: float | None = None) -> float:
    """실수형 환경변수를 읽고 필요하면 하한선을 검사한다.
    timeout 같은 설정값이 0 이하로 들어와 runtime이 보정할 수 없는 상태를 여기서 막는다.
    """
    raw = _get_env_str(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}")
    return value


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_local_models_root() -> Path:
    """로컬 모델 자산이 저장된 루트 디렉토리를 결정합니다."""
    raw = _get_env_str("NTIS_LOCAL_MODELS_ROOT", str(_REPO_ROOT / "Models"))
    return Path(raw).expanduser().resolve()


LOCAL_MODELS_ROOT = str(_resolve_local_models_root())


def _local_models_path(*parts: str) -> str:
    """로컬 모델 루트 하위의 절대 경로를 생성합니다."""
    return str((Path(LOCAL_MODELS_ROOT).joinpath(*parts)).resolve())


def _resolve_local_hf_model_snapshot(model_dir_name: str) -> str:
    """Hugging Face 캐시 스타일의 로컬 모델 디렉토리에서 실제 로드 가능한 스냅샷 경로를 찾습니다."""
    model_root = Path(LOCAL_MODELS_ROOT).joinpath(model_dir_name).resolve()
    if not model_root.exists():
        raise ValueError(f"Local model directory not found: {model_root}")

    if (model_root / "config.json").exists():
        return str(model_root)

    ref_main = model_root / "refs" / "main"
    if ref_main.exists():
        snapshot_name = ref_main.read_text(encoding="utf-8").strip()
        if not snapshot_name:
            raise ValueError(f"Local model ref is empty: {ref_main}")
        snapshot_dir = (model_root / "snapshots" / snapshot_name).resolve()
        if (snapshot_dir / "config.json").exists():
            return str(snapshot_dir)
        raise ValueError(f"Local model snapshot missing config.json: {snapshot_dir}")

    raise ValueError(
        f"Local model directory is not directly loadable and has no refs/main snapshot pointer: {model_root}"
    )


def _get_env_or_local_model(env_name: str, default_model_dir_name: str) -> str:
    """환경 변수 설정이 있으면 사용하고, 없으면 로컬 기본 경로를 반환합니다."""
    raw = os.getenv(env_name)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return _resolve_local_hf_model_snapshot(default_model_dir_name)

# Qdrant / Embedding 설정
QDRANT_HOST  = _get_env_str("QDRANT_HOST", "203.250.234.159")
QDRANT_PORT  = _get_env_int("QDRANT_PORT", 8005, min_value=1)
EMBED_MODEL  = _get_env_or_local_model("EMBEDDING_MODEL", "multilingual-e5-large-instruct")

# 백업용 Qdrant / Embedding 설정
QDRANT_HOST_B  = _get_env_str("QDRANT_HOST_B", QDRANT_HOST)
QDRANT_PORT_B  = _get_env_int("QDRANT_PORT_B", QDRANT_PORT, min_value=1)
EMBED_MODEL_B  = _get_env_or_local_model("EMBEDDING_MODEL_B", "multilingual-e5-large")


def _split_csv(value: str | None, default: list[str]) -> list[str]:
    """CSV 형식의 환경 변수를 리스트로 변환합니다."""
    if value is None:
        return list(default)
    value = value.strip()
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


RAG_COLLECTION_ALLOWLIST = _split_csv(
    os.getenv("RAG_COLLECTION_ALLOWLIST"),
    ["ntis_project_v1", "ntis_perf_v1", "ntis_supports_v1"],
)

# Triton 추론 서버 설정
TRITON_URL         = _get_env_str("TRITON_URL", "203.250.234.159:8001")
DEFAULT_MODEL_NAME = _get_env_str("TRITON_MODEL", "gpt_triton_0")
TOKENIZER_MAP = {
    "gemma_triton_0": ("gemma-3-27b-it"),
    "gpt_triton_0": _local_models_path("gpt-oss-120b"),
}
SOLAR_TOKENIZER_NAME_OR_PATH = _get_env_str("SOLAR_TOKENIZER_NAME_OR_PATH", _get_env_str("SOLAR_VLLM_MODEL", "/model"))

# =============================================================================
# 하이퍼파라미터 설정 (RAG 성능과 품질에 직접적인 영향을 미칩니다)
# =============================================================================

# [1] TOP_K_BASE: 초기 검색 단계(Retrieval)에서 벡터 DB로부터 가져올 후보 문서의 개수입니다.
# - 영향: 값이 클수록 정답이 포함될 확률(Recall)이 높아지지만, 검색 속도가 느려지고 Rerank 단계의 부담이 커집니다.
TOP_K_BASE       = 300

# [2] TOP_K_RETURN: 재정렬(Rerank) 과정을 거친 후 최종적으로 LLM 프롬프트에 전달할 문서의 개수입니다.
# - 영향: 너무 많으면 LLM의 문맥 파악 능력이 떨어지는 'Lost in the middle' 현상이 발생할 수 있고,
#   너무 적으면 답변에 필요한 정보가 부족해질 수 있습니다.
TOP_K_RETURN     = 20

# [3] SCORE_THRESHOLD: 검색된 결과 중 유효하다고 판단할 최소 유사도 점수입니다.
# - 영향: 임계값이 높으면 관련성이 아주 높은 문서만 선택되어 정확도가 올라가지만,
#   답변에 필요한 문서가 제외될 위험이 있습니다. 낮으면 노이즈가 섞일 확률이 높아집니다.
SCORE_THRESHOLD  = 0.20

# [4] TEMPERATURE: LLM 답변의 창의성/무작위성을 조절합니다 (0.0 ~ 1.0).
# - 영향: RAG 시스템에서는 지식에 기반한 정확한 답변이 중요하므로 낮은 값(0.2)을 유지하여 
#   모델의 환각(Hallucination) 현상을 억제합니다.
TEMPERATURE      = 0.2

# [5] TOP_P: 답변 생성 시 누적 확률이 P 이내인 단어들 중에서 선택합니다.
# - 영향: 1.0에 가까울수록 다양한 단어를 선택하며, 0.8 정도의 설정은 답변의 일관성과 품질 사이의 균형을 잡습니다.
TOP_P            = 0.8

# [6] RAG_EVIDENCE_TOKEN_BUDGET: 프롬프트 구성 시 증거(Context)로 할당할 최대 토큰 예산입니다.
# - 영향: 이 예산을 초과하는 문서는 아무리 유사도가 높아도 잘려나갑니다. 
#   전체 프롬프트 길이 제한 내에서 최대한 많은 정보를 담을 수 있도록 최적화된 값이 필요합니다.
RAG_EVIDENCE_TOKEN_BUDGET = _get_env_int("RAG_EVIDENCE_TOKEN_BUDGET", 20000, min_value=1)

# [7] MAX_CONTEXT_CHARS: 프롬프트에 포함될 전체 컨텍스트의 최대 글자 수입니다.
# - 영향: 토큰 예산과 별개로 글자 수 단위의 하드 리밋을 설정하여 예상치 못한 긴 입력으로 인한 오류를 방지합니다.
MAX_CONTEXT_CHARS = _get_env_int("MAX_CONTEXT_CHARS", 24000, min_value=1)

# [8] SNIPPET_MAX_CHARS: 개별 문서(Snippet) 하나가 가질 수 있는 최대 글자 수입니다.
# - 영향: 너무 긴 문서는 핵심 내용이 희석되거나 예산을 독점할 수 있으므로 적절한 수준(8000자)에서 제한합니다.
SNIPPET_MAX_CHARS = 8000

# [9] MAX_DOC_SENTENCES / MAX_DOC_TOKENS: 개별 문서의 상세 제약 조건입니다.
# - 영향: 문서 하나가 너무 많은 분량을 차지하지 않도록 하여, 컨텍스트 내에 더 다양한 문서가 포함되도록 유도합니다.
MAX_DOC_SENTENCES = _get_env_int("MAX_DOC_SENTENCES", 12, min_value=1)
MAX_DOC_TOKENS = _get_env_int("MAX_DOC_TOKENS", 800, min_value=1)

# =============================================================================

DEFAULT_MAX_TOKENS = _get_env_int("MAX_TOKENS", 16000, min_value=1)
FUZZ_MIN         = 55
SUMMARY_DOC_SENTENCES = _get_env_int("SUMMARY_DOC_SENTENCES", 6, min_value=1)
SUMMARY_DOC_TOKENS = _get_env_int("SUMMARY_DOC_TOKENS", 240, min_value=1)

RAG_OVERFLOW_QUEUE_SIZE = _get_env_int("RAG_OVERFLOW_QUEUE_SIZE", 4, min_value=0)
RAG_CONTEXT_COMPRESS_MIN_SCORE = _get_env_float("RAG_CONTEXT_COMPRESS_MIN_SCORE", 0.0)
RAG_CONTEXT_COMPRESS_MAX_DOCS = _get_env_int("RAG_CONTEXT_COMPRESS_MAX_DOCS", 4, min_value=0)
RAG_CONTEXT_BODY_MAX_SHARE = _get_env_float("RAG_CONTEXT_BODY_MAX_SHARE", 0.6, min_value=0.0)
RAG_EVIDENCE_EXACT_VERIFY_FLOOR = _get_env_int("RAG_EVIDENCE_EXACT_VERIFY_FLOOR", 512, min_value=1)
RAG_EVIDENCE_EXACT_VERIFY_RATIO = _get_env_float("RAG_EVIDENCE_EXACT_VERIFY_RATIO", 0.1, min_value=0.0)
RAW_PAYLOAD_RECENT_ANCHOR_LIMIT = _get_env_int("RAW_PAYLOAD_RECENT_ANCHOR_LIMIT", 3, min_value=1)
RAW_PAYLOAD_SCHEMA_VERSION = _get_env_str("RAW_PAYLOAD_SCHEMA_VERSION", "v1")
RAW_PAYLOAD_COMPRESSION_CODEC = _get_env_str("RAW_PAYLOAD_COMPRESSION_CODEC", "gzip")

# Redis 캐시 설정
REDIS_URL = _get_env_str("REDIS_URL", "redis://redis8:6379")
REDIS_TTL = _get_env_int("REDIS_TTL", 3600, min_value=1)
MAX_TOP_K_SIZE = _get_env_int("MAX_TOP_K_SIZE", 20, min_value=1)

PROMPT_OVERHEAD_TOKENS = _get_env_int("RAG_PROMPT_OVERHEAD_TOKENS", 900, min_value=0)
CTX_SAFETY_MARGIN = _get_env_int("RAG_CTX_SAFETY_MARGIN", 256, min_value=0)
CTX_MIN_BUDGET = _get_env_int("RAG_CTX_MIN_BUDGET", 512, min_value=1)
DEFAULT_MAX_MODEL_LEN = _get_env_int("DEFAULT_MAX_MODEL_LEN", 32768, min_value=1)

def get_ctx_token_budget(model_name: str, *, max_output_tokens: int | None = None) -> int:
    """모델의 최대 컨텍스트 길이에서 오버헤드와 출력 토큰을 제외한 순수 증거(Evidence) 예산을 계산합니다."""
    model_ctx = MODEL_MAX_CONTEXT.get(model_name, DEFAULT_MAX_MODEL_LEN)

    if max_output_tokens is None:
        max_output_tokens = MAX_TOKENS.get(model_name, DEFAULT_MAX_TOKENS)

    avail = model_ctx - PROMPT_OVERHEAD_TOKENS - int(max_output_tokens) - CTX_SAFETY_MARGIN

    budget = int(avail)
    logger.debug("CTX token budget calculated: {}", budget)
    return max(int(CTX_MIN_BUDGET), budget)

MODEL_MAX_CONTEXT = {
    "solar_vllm_0": _get_env_int("SOLAR_MAX_MODEL_LEN", DEFAULT_MAX_MODEL_LEN, min_value=1),
    "gemma_triton_0": _get_env_int("GEMMA_MAX_MODEL_LEN", DEFAULT_MAX_MODEL_LEN, min_value=1),
    "gpt_triton_0": _get_env_int("GPT_OSS_MAX_MODEL_LEN", DEFAULT_MAX_MODEL_LEN, min_value=1),
}
MAX_TOKENS = {
    "solar_vllm_0": _get_env_int("SOLAR_MAX_TOKENS", DEFAULT_MAX_TOKENS, min_value=1),
    "gemma_triton_0": _get_env_int("GEMMA_MAX_TOKENS", DEFAULT_MAX_TOKENS, min_value=1),
    "gpt_triton_0": _get_env_int("GPT_OSS_MAX_TOKENS", DEFAULT_MAX_TOKENS, min_value=1),
}


def get_model_max_output_tokens(model_name: str) -> int:
    """모델별 최대 생성 토큰 설정을 반환합니다."""
    return int(MAX_TOKENS.get(model_name, DEFAULT_MAX_TOKENS))


def _get_timeout_env(name: str, default: int) -> int:
    """타임아웃 설정을 안전하게 읽어옵니다."""
    return _get_env_int(name, default, min_value=1)


def _get_model_timeout_pair(
        *,
        model_env_prefix: str,
        request_type: str,
        default_first: int,
        default_idle: int,
        deprecated_env_prefix: str | None = None,
) -> tuple[int, int]:
    """모델별 첫 토큰 응답 및 유휴 타임아웃 쌍을 계산합니다."""
    request_env_prefix = f"TRITON_{model_env_prefix}_{request_type}"

    first = _get_timeout_env(f"{request_env_prefix}_TIMEOUT_FIRST", default_first)
    idle = _get_timeout_env(f"{request_env_prefix}_TIMEOUT_IDLE", default_idle)

    if deprecated_env_prefix:
        deprecated_first = os.getenv(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_FIRST")
        deprecated_idle = os.getenv(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_IDLE")
        if deprecated_first is not None:
            logger.warning(
                "[DEPRECATED] {}_{}_TIMEOUT_FIRST is deprecated and will be removed in v0.5.0. "
                "Use {}_TIMEOUT_FIRST instead.",
                deprecated_env_prefix,
                request_type,
                request_env_prefix,
            )
            first = _get_env_int(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_FIRST", default_first, min_value=1)
        if deprecated_idle is not None:
            logger.warning(
                "[DEPRECATED] {}_{}_TIMEOUT_IDLE is deprecated and will be removed in v0.5.0. "
                "Use {}_TIMEOUT_IDLE instead.",
                deprecated_env_prefix,
                request_type,
                request_env_prefix,
            )
            idle = _get_env_int(f"{deprecated_env_prefix}_{request_type}_TIMEOUT_IDLE", default_idle, min_value=1)

    return first, idle


GEMMA_STREAM_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GEMMA_TRITON_0",
    request_type="STREAM",
    default_first=4,
    default_idle=20,
    deprecated_env_prefix="GEMMA",
)
GEMMA_SYNC_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GEMMA_TRITON_0",
    request_type="SYNC",
    default_first=GEMMA_STREAM_TIMEOUTS[0],
    default_idle=GEMMA_STREAM_TIMEOUTS[1],
    deprecated_env_prefix="GEMMA",
)
GPT_OSS_STREAM_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GPT_OSS_TRITON_0",
    request_type="STREAM",
    default_first=10,
    default_idle=20,
)
GPT_OSS_SYNC_TIMEOUTS = _get_model_timeout_pair(
    model_env_prefix="GPT_OSS_TRITON_0",
    request_type="SYNC",
    default_first=GPT_OSS_STREAM_TIMEOUTS[0],
    default_idle=GPT_OSS_STREAM_TIMEOUTS[1],
)

TRITON_TIMEOUTS = {
    "gemma_triton_0": {
        "stream": GEMMA_STREAM_TIMEOUTS,
        "sync": GEMMA_SYNC_TIMEOUTS,
    },
    "gpt_triton_0": {
        "stream": GPT_OSS_STREAM_TIMEOUTS,
        "sync": GPT_OSS_SYNC_TIMEOUTS,
    },
}

# 벤치마킹 및 운영 로그 디렉토리 설정
LOG_DIR = Path(_get_env_str("RAG_BENCH_LOG_DIR", "./rag_bench_logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault(
    "CTX_TOKEN_BUDGET",
    str(
        get_ctx_token_budget(
            DEFAULT_MODEL_NAME,
            max_output_tokens=get_model_max_output_tokens(DEFAULT_MODEL_NAME),
        )
    ),
)
os.environ.setdefault("SNIPPET_MAX_CHARS", str(SNIPPET_MAX_CHARS))


@dataclass(frozen=True)
class SolarVLLMConfig:
    """Solar OpenAI-compat 제공자 접속에 필요한 설정을 그룹화합니다."""
    model_name: str
    base_url: str
    api_key: str
    timeout: float

    @classmethod
    def from_env(cls) -> "SolarVLLMConfig":
        """환경 변수로부터 Solar VLLM 설정을 읽어옵니다."""
        model_name = _get_env_str("SOLAR_VLLM_MODEL", "/model")
        base_url = _get_env_str("SOLAR_VLLM_BASE_URL", "http://203.250.234.159:8010/v1")
        api_key = _get_env_str("SOLAR_VLLM_API_KEY", "EMPTY")

        if not model_name:
            raise ValueError("SOLAR_VLLM_MODEL must not be empty")
        if not base_url:
            raise ValueError("SOLAR_VLLM_BASE_URL must not be empty")

        timeout = _get_env_float("SOLAR_VLLM_TIMEOUT", 120.0, min_value=0.000001)

        return cls(model_name=model_name, base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout)


SOLAR_VLLM_CONFIG = SolarVLLMConfig.from_env()

"""시스템 로깅 부트스트랩 — 가독성 높은 콘솔 포맷 + 표준 logging 통합.

이 모듈은 Loguru 싱크를 한 곳에서 구성해 전체 파이프라인 로그가 일관되고
보기 좋은 형태로 출력되도록 한다. 호출부(각 agent 노드)는 그대로 두고
**중앙 포맷만** 바꿔 모든 로그의 가독성을 끌어올리는 게 목적이다.

콘솔 출력 한 줄 구조 (컬럼 정렬):

    HH:mm:ss.SSS │ • INFO     │ pipeline.agentic_workflow:node_planner:131 │ <메시지>
    └─ 시각        └─ 레벨(아이콘+이름)  └─ 위치(모듈:함수:줄, 좌측 말줄임)        └─ 본문

설계 원칙:
    - **레벨별 아이콘 + 색상**: INFO/SUCCESS/WARNING/ERROR가 한눈에 구분된다.
    - **컬럼 정렬**: 시각·레벨·위치 폭을 고정해 메시지 시작점이 가지런하다.
    - **위치 단축**: `apps.` 접두를 떼고 폭 초과 시 앞부분을 `…`로 줄여 길이를 통제.
    - **경고/오류 강조**: 본문이 레벨 색으로 물들어 WARNING(노랑)·ERROR(빨강)이 튄다.
    - **상세 파일 싱크(옵션)**: RAG_LOG_FILE 지정 시 색 없는 상세 로그를 회전 저장.

환경변수:
    RAG_LOG_LEVEL        콘솔 최소 레벨 (기본 INFO). TRACE/DEBUG/INFO/WARNING/ERROR.
    RAG_LOG_COLOR        auto(기본)/true/false — ANSI 색상. auto는 TTY 자동 감지.
    RAG_LOG_ASCII        auto(기본)/true/false — 박스·아이콘을 ASCII로 강제(콘솔 호환).
    RAG_LOG_FILE         지정 시 해당 경로로 상세 로그 파일 싱크 추가(회전).
    RAG_LOG_FILE_LEVEL   파일 싱크 최소 레벨 (기본 = RAG_LOG_LEVEL).
    RAG_LOG_ROTATION     파일 회전 기준 (기본 "10 MB").
    RAG_LOG_RETENTION    파일 보관 기간/개수 (기본 "10 days").
    RAG_LOG_COMPRESSION  회전 파일 압축 (기본 "zip", 빈 값이면 비압축).
    RAG_LOG_BACKTRACE    true(기본)/false — 예외 시 확장 트레이스백.
    RAG_LOG_DIAGNOSE     false(기본)/true — 변수값까지 노출(운영 민감정보 주의).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# 컬럼 폭 / 레벨 아이콘
# ---------------------------------------------------------------------------

# 위치 컬럼(모듈:함수:줄) 고정 폭. 초과 시 앞부분을 말줄임으로 줄인다.
_LOC_WIDTH = 42

# 레벨별 단색 아이콘. 정렬을 위해 모두 1칸(단일 폭) 글자만 사용한다.
_ICONS_UNICODE = {
    "TRACE": "·",
    "DEBUG": "◦",
    "INFO": "•",
    "SUCCESS": "✔",
    "WARNING": "▲",
    "ERROR": "✖",
    "CRITICAL": "✸",
}
_ICONS_ASCII = {
    "TRACE": ".",
    "DEBUG": ".",
    "INFO": "*",
    "SUCCESS": "+",
    "WARNING": "!",
    "ERROR": "x",
    "CRITICAL": "X",
}


# ---------------------------------------------------------------------------
# env 헬퍼 (settings.py를 import하지 않는다 — 순환 import 방지)
# ---------------------------------------------------------------------------

def _env_str(name: str, default: str) -> str:
    return str(os.getenv(name, default) or "").strip()


def _env_tristate(name: str, default: str = "auto") -> str:
    """auto/true/false 세 갈래 env. 빈 값은 default로."""
    raw = _env_str(name, default).lower()
    if raw in {"auto", "true", "false", "1", "0", "yes", "no", "on", "off"}:
        return {"1": "true", "yes": "true", "on": "true",
                "0": "false", "no": "false", "off": "false"}.get(raw, raw)
    return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name, "true" if default else "false").lower()
    return raw not in {"0", "false", "no", "off", ""}


# ---------------------------------------------------------------------------
# 포맷 보조
# ---------------------------------------------------------------------------

def _supports_unicode(stream: Any) -> bool:
    """스트림 인코딩이 박스·아이콘 글자를 표현할 수 있는지 확인."""
    enc = getattr(stream, "encoding", None) or ""
    try:
        "│✔▲✖".encode(enc)
        return True
    except (LookupError, UnicodeEncodeError, TypeError):
        return False


def _shorten_name(name: str) -> str:
    """모듈 경로에서 공통 접두(`apps.`)를 떼어 짧게."""
    if name.startswith("apps."):
        return name[len("apps."):]
    return name


def _fit_left(text: str, width: int) -> str:
    """폭에 맞춰 좌측 말줄임 + 우측 패딩. 위치 컬럼 정렬용.

    위치 문자열은 끝부분(함수:줄)이 더 중요하므로 앞을 잘라낸다.
    """
    if len(text) <= width:
        return text.ljust(width)
    return "…" + text[-(width - 1):]


def _make_console_format(use_unicode: bool) -> Callable[[Any], str]:
    """레벨 아이콘·정렬을 반영하는 Loguru 포맷 함수를 만든다.

    Loguru는 콜러블 포맷이 반환한 템플릿의 색 마크업만 해석하고,
    `{message}`·`{extra[..]}` 같은 필드 **값**은 그대로 치환한다(마크업 재해석 X).
    덕분에 본문에 `{`, `<`, `[` 등이 섞여도 안전하다.
    """
    sep = "│" if use_unicode else "|"
    icons = _ICONS_UNICODE if use_unicode else _ICONS_ASCII

    def _fmt(record: Any) -> str:
        level_name = record["level"].name
        record["extra"]["icon"] = icons.get(level_name, " ")
        loc = f"{_shorten_name(record['name'])}:{record['function']}:{record['line']}"
        record["extra"]["loc"] = _fit_left(loc, _LOC_WIDTH)
        return (
            "<green>{time:HH:mm:ss.SSS}</green> "
            f"{sep} <level>{{extra[icon]}} {{level: <8}}</level> "
            f"{sep} <cyan>{{extra[loc]}}</cyan> "
            f"{sep} <level>{{message}}</level>"
            "\n{exception}"
        )

    return _fmt


def _file_format() -> str:
    """파일 싱크용 상세 포맷 (색 없음, 풀 타임스탬프 + 프로세스/스레드)."""
    return (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{process.name}:{thread.name} | "
        "{name}:{function}:{line} - {message}"
    )


# ---------------------------------------------------------------------------
# 표준 logging → Loguru 브릿지
# ---------------------------------------------------------------------------

class InterceptHandler(logging.Handler):
    """표준 logging 레코드를 Loguru로 넘겨 단일 포맷으로 통합한다.

    uvicorn·httpx 등 표준 logging을 쓰는 라이브러리 로그도 동일한
    예쁜 포맷으로 출력되도록 root 핸들러로 설치한다.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: Any = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# ---------------------------------------------------------------------------
# 부트스트랩
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """Loguru 싱크를 구성하고 표준 logging을 가로채 단일 포맷으로 통합한다.

    settings.py import 시점에 1회 호출된다. 멱등(remove로 기존 싱크 제거 후 재설치).
    """
    level = _env_str("RAG_LOG_LEVEL", "INFO").upper() or "INFO"

    color_mode = _env_tristate("RAG_LOG_COLOR", "auto")
    colorize: Optional[bool] = None if color_mode == "auto" else (color_mode == "true")

    ascii_mode = _env_tristate("RAG_LOG_ASCII", "auto")
    if ascii_mode == "auto":
        use_unicode = _supports_unicode(sys.stderr)
    else:
        use_unicode = ascii_mode == "false"

    backtrace = _env_bool("RAG_LOG_BACKTRACE", True)
    diagnose = _env_bool("RAG_LOG_DIAGNOSE", False)

    logger.remove()

    # 1) 콘솔(stderr) — 가독성 우선 포맷
    logger.add(
        sys.stderr,
        level=level,
        format=_make_console_format(use_unicode),
        colorize=colorize,
        backtrace=backtrace,
        diagnose=diagnose,
    )

    # 2) 파일(옵션) — RAG_LOG_FILE 지정 시 색 없는 상세 로그를 회전 저장
    log_file = _env_str("RAG_LOG_FILE", "")
    if log_file:
        compression = _env_str("RAG_LOG_COMPRESSION", "zip") or None
        logger.add(
            log_file,
            level=_env_str("RAG_LOG_FILE_LEVEL", level).upper() or level,
            format=_file_format(),
            rotation=_env_str("RAG_LOG_ROTATION", "10 MB"),
            retention=_env_str("RAG_LOG_RETENTION", "10 days"),
            compression=compression,
            encoding="utf-8",
            enqueue=True,
            backtrace=backtrace,
            diagnose=diagnose,
        )

    # 3) 표준 logging(uvicorn 등) → Loguru 라우팅
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # 4) 초기화 요약 한 줄 — 활성 설정을 그대로 보여줘 디버깅을 돕는다.
    logger.info(
        "로깅 초기화 완료 — level={} color={} glyph={} file={}",
        level,
        color_mode,
        "unicode" if use_unicode else "ascii",
        log_file or "off(콘솔 전용)",
    )

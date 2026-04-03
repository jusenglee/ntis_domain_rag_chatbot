from __future__ import annotations

import os

from apps.api.contracts.workflow_models import PLANNER_SCHEMA_VERSION
from apps.platform.settings import MAX_TOP_K_SIZE

PLANNER_PROMPT_DEFAULTS = {
    "stage1": "v2",
    "stage15": "v1",
    "stage2": "v2",
}

PLANNER_STAGEWISE_ENABLED = True
PLANNER_DISABLE_THINKING = str(os.getenv("PLANNER_DISABLE_THINKING", "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PLANNER_STAGE1_PROMPT_VERSION = os.getenv("PLANNER_STAGE1_PROMPT_VERSION", PLANNER_PROMPT_DEFAULTS["stage1"]).strip()
PLANNER_STAGE15_PROMPT_VERSION = os.getenv("PLANNER_STAGE15_PROMPT_VERSION", PLANNER_PROMPT_DEFAULTS["stage15"]).strip()
PLANNER_STAGE2_PROMPT_VERSION = os.getenv("PLANNER_STAGE2_PROMPT_VERSION", PLANNER_PROMPT_DEFAULTS["stage2"]).strip()
PLANNER_TEMPERATURE = float(os.getenv("PLANNER_TEMPERATURE", "0.0"))
PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS = {
    "pjt_id",
    "pjt_no",
    "doi",
    "issn",
    "rst_id",
    "paper_id",
}

# Planner runtime keeps its own source-of-truth for schema/top-k settings even though
# the underlying schema and platform limit are defined by shared contracts.
PLANNER_RUNTIME_SCHEMA_VERSION = PLANNER_SCHEMA_VERSION
PLANNER_RUNTIME_MAX_TOP_K_SIZE = MAX_TOP_K_SIZE

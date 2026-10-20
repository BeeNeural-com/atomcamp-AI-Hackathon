"""Runtime configuration read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
PIPELINE_VERSION = "0.1.0"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Flags:
    """Pipeline switches. Used by the ablation benchmark."""

    witness_a: bool = True
    validators: bool = True
    relook: bool = True
    memory: bool = True

    def as_dict(self) -> dict:
        return {
            "witness_a": self.witness_a,
            "validators": self.validators,
            "relook": self.relook,
            "memory": self.memory,
        }


@dataclass(frozen=True)
class Settings:
    app_env: str = "local"
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'beeproof.db').as_posix()}"
    store: str = "local"  # local | gcs
    local_store_dir: Path = BACKEND_DIR / "data" / "store"
    bucket: str = ""
    project: str = ""
    extractor: str = "heuristic"  # heuristic | gemini
    layout_provider: str = "auto"  # auto | pymupdf | docai | none
    gemini_model: str = ""
    gemini_location: str = "us-central1"
    docai_location: str = "us"
    docai_processor_id: str = ""
    tau: float = 0.80
    page_concurrency: int = 8
    max_upload_bytes: int = 20 * 1024 * 1024
    max_document_pages: int = 30
    max_image_pixels: int = 40_000_000
    max_relook_loops: int = 2
    max_relook_calls_per_job: int = 60
    max_job_attempts: int = 3
    worker_mode: str = "inline"  # inline | off
    queue_url: str = ""  # redis://host:6379/0; empty = database polling
    # Cloud Tasks (production): API enqueues HTTP tasks to the private worker service
    cloud_tasks_queue: str = ""  # projects/<p>/locations/<r>/queues/<q>
    worker_service_url: str = ""  # https://beeproof-worker-....run.app
    tasks_invoker_service_account: str = ""  # SA whose OIDC token Cloud Tasks presents to the worker
    tasks_dispatch_deadline_seconds: int = 1800
    # Database
    auto_create_tables: bool = True  # production: False, schema is owned by Alembic migrations
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_recycle_seconds: int = 1800
    log_format: str = "text"  # text | json (Cloud Logging)
    worker_poll_seconds: float = 1.0
    stale_job_seconds: int = 900
    cors_origins: tuple[str, ...] = ("*",)
    rate_limit_per_min: int = 120  # mutating requests per client IP; 0 disables
    bench_source_csv: Path = REPO_DIR / "ps06_sample_rate_sheets.csv"
    bench_dir: Path = BACKEND_DIR / "data" / "bench"
    gemini_usd_per_page: float = 0.0  # 0 means unknown; cost stays null
    docai_usd_per_page: float = 0.0
    flags: Flags = field(default_factory=Flags)

    def with_flags(self, **overrides) -> Settings:
        return replace(self, flags=replace(self.flags, **overrides))


def load_settings() -> Settings:
    _load_dotenv(BACKEND_DIR / ".env")
    d = Settings()
    origins = os.environ.get("CORS_ORIGINS")
    return Settings(
        app_env=os.environ.get("APP_ENV", d.app_env),
        database_url=os.environ.get("DATABASE_URL") or d.database_url,
        store=os.environ.get("STORE", d.store),
        local_store_dir=Path(os.environ.get("LOCAL_STORE_DIR") or d.local_store_dir),
        bucket=os.environ.get("BUCKET") or os.environ.get("GCS_BUCKET", d.bucket),
        project=(
            os.environ.get("PROJECT")
            or os.environ.get("GCP_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT", d.project)
        ),
        extractor=os.environ.get("EXTRACTOR", d.extractor),
        layout_provider=os.environ.get("LAYOUT_PROVIDER", d.layout_provider),
        gemini_model=os.environ.get("GEMINI_MODEL", d.gemini_model),
        gemini_location=(
            os.environ.get("GEMINI_LOCATION") or os.environ.get("VERTEX_AI_LOCATION") or d.gemini_location
        ),
        docai_location=os.environ.get("DOCAI_LOCATION", d.docai_location),
        docai_processor_id=os.environ.get("DOCAI_PROCESSOR_ID", d.docai_processor_id),
        tau=_float("TAU", d.tau),
        page_concurrency=_int("PAGE_CONCURRENCY", d.page_concurrency),
        max_upload_bytes=_int("MAX_UPLOAD_BYTES", d.max_upload_bytes),
        max_document_pages=_int("MAX_DOCUMENT_PAGES", d.max_document_pages),
        max_image_pixels=_int("MAX_IMAGE_PIXELS", d.max_image_pixels),
        max_relook_loops=_int("MAX_REPAIR_ATTEMPTS", d.max_relook_loops),
        max_relook_calls_per_job=_int("MAX_REPAIR_CALLS_PER_JOB", d.max_relook_calls_per_job),
        max_job_attempts=_int("MAX_JOB_ATTEMPTS", d.max_job_attempts),
        worker_mode=os.environ.get("WORKER_MODE", d.worker_mode),
        queue_url=os.environ.get("QUEUE_URL") or os.environ.get("REDIS_URL", d.queue_url),
        cloud_tasks_queue=os.environ.get("CLOUD_TASKS_QUEUE", d.cloud_tasks_queue),
        worker_service_url=os.environ.get("WORKER_SERVICE_URL", d.worker_service_url).rstrip("/"),
        tasks_invoker_service_account=os.environ.get("TASKS_INVOKER_SERVICE_ACCOUNT", d.tasks_invoker_service_account),
        tasks_dispatch_deadline_seconds=_int("TASKS_DISPATCH_DEADLINE_SECONDS", d.tasks_dispatch_deadline_seconds),
        auto_create_tables=_bool("AUTO_CREATE_TABLES", d.auto_create_tables),
        db_pool_size=_int("DB_POOL_SIZE", d.db_pool_size),
        db_max_overflow=_int("DB_MAX_OVERFLOW", d.db_max_overflow),
        db_pool_recycle_seconds=_int("DB_POOL_RECYCLE_SECONDS", d.db_pool_recycle_seconds),
        log_format=os.environ.get("LOG_FORMAT", d.log_format),
        worker_poll_seconds=_float("WORKER_POLL_SECONDS", d.worker_poll_seconds),
        stale_job_seconds=_int("STALE_JOB_SECONDS", d.stale_job_seconds),
        cors_origins=tuple(o.strip() for o in origins.split(",")) if origins else d.cors_origins,
        rate_limit_per_min=_int("RATE_LIMIT_PER_MIN", d.rate_limit_per_min),
        bench_source_csv=Path(os.environ.get("BENCH_SOURCE_CSV") or d.bench_source_csv),
        bench_dir=Path(os.environ.get("BENCH_DIR") or d.bench_dir),
        gemini_usd_per_page=_float("GEMINI_USD_PER_PAGE", d.gemini_usd_per_page),
        docai_usd_per_page=_float("DOCAI_USD_PER_PAGE", d.docai_usd_per_page),
        flags=Flags(
            witness_a=_bool("FLAG_WITNESS_A", True),
            validators=_bool("FLAG_VALIDATORS", True),
            relook=_bool("FLAG_RELOOK", True),
            memory=_bool("FLAG_MEMORY", True),
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Override settings (tests)."""
    global _settings
    _settings = settings

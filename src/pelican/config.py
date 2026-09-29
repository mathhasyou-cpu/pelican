"""Настройки из окружения (и `.env` в корне репозитория)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Postgres: корпус, разметка, очередь запросов и готовые отчёты.
    database_url: str = "postgresql://pelican:pelican@127.0.0.1:5436/pelican"
    # Состояние приложения рядом с базой: кэши живого поиска и следа корпуса, файловые
    # замки.
    data_dir: Path = Field(default=Path("data"))
    # Шарды векторов корпуса (`shard-*.npz`, ~22 ГБ). Отдельно от `data_dir`: их пишет
    # докачка, а читает приложение, и на стенде каталог монтируется только на чтение.
    # Пусто — `<data_dir>/emb-science`.
    emb_dir: Path | None = None
    # Нативный `pelican.shard_server` для `api` в контейнере: проход по шардам через bind
    # mount втрое медленнее (docs/weak-demo.md §3). Пусто — шарды читает этот процесс.
    shards_url: str = ""

    # Локальная модель — OpenAI-совместимый сервер (LM Studio). Одна и та же точка
    # отдаёт и генерацию, и эмбеддинги.
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_model: str = "google/gemma-4-12b-qat"
    # Сколько вызовов держать в полёте. Сервер обрабатывает их по одному, глубина нужна,
    # чтобы между вызовами не простаивала GPU.
    llm_pipeline_depth: int = 3
    llm_timeout_s: float = 180.0
    # Reasoning у gemma-4 включён по умолчанию и на пакетной разметке стоит в десять раз
    # больше времени при том же качестве.
    llm_reasoning_effort: str = "none"
    embedding_model: str = "google/embedding-gemma-300m"
    embedding_batch_size: int = 64

    github_token: str = ""
    # EPO OPS (патентные базы). Без обоих ключей патентный канал выключен.
    epo_ops_key: str = ""
    epo_ops_secret: str = ""
    # Контакт в User-Agent: вежливость к открытым API (arXiv, OpenAlex).
    contact: str = ""

    # Категории arXiv для суточного окна и бэкфилла: про технологии, а не про науку
    # целиком (`all:*` arXiv отвергает, а трендов технологий в чистой математике нет).
    arxiv_categories: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["cs.*", "eess.*", "stat.ML", "cond-mat.mtrl-sci", "q-bio.*"]
    )
    # Срезы OpenAlex — готовые фрагменты фильтра: материалы, энергетика, химия, химтех
    # (почти не дублируют arXiv) и CS с инженерией (ради институтов, стран и фондов).
    openalex_filters: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "primary_topic.field.id:fields/25",
            "primary_topic.field.id:fields/21",
            "primary_topic.field.id:fields/16",
            "primary_topic.field.id:fields/15",
            "primary_topic.field.id:fields/17",
            "primary_topic.field.id:fields/22",
        ]
    )
    # Ключ OpenAlex — это бюджет, а не доступ: $0.0001 за запрос, свободный ключ даёт
    # $1 в сутки. Без ключа источник работает, но бюджет кончается раньше.
    openalex_api_key: str = ""

    @field_validator("arxiv_categories", "openalex_filters", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # Списки в `.env` — через запятую. `NoDecode` на поле обязателен: иначе
        # pydantic-settings делает json.loads() до валидатора и падает на CSV.
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("llm_pipeline_depth")
    @classmethod
    def _sane_depth(cls, v: int) -> int:
        if v < 1:
            raise ValueError("llm_pipeline_depth must be >= 1")
        return v

    @property
    def user_agent(self) -> str:
        return f"pelican/0.1 ({self.contact or 'weak signals radar'})"

    @property
    def storage_target(self) -> str:
        return self.database_url

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir if self.data_dir.is_absolute() else REPO_ROOT / self.data_dir


settings = Settings()
DATA_DIR = settings.resolved_data_dir
EMB_DIR = settings.emb_dir or DATA_DIR / "emb-science"

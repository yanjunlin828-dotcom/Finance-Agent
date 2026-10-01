"""S2指标观察和计算结果的不可覆盖SQLite存储。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Iterable

from finresearch.contracts import CalculationResult, MetricDefinition, MetricObservation


class DuplicateMetricRecordError(RuntimeError):
    """唯一业务记录已存在；存储不执行静默覆盖。"""


class MetricSourceConflictError(RuntimeError):
    """Multiple independent or incomparable facts require explicit resolution."""


def _fact_key(item: MetricObservation) -> tuple:
    return (item.company_id, item.metric_id, item.fiscal_year, item.period_kind,
            item.period_start, item.period_end, item.observed_at, item.currency,
            item.statement_scope, item.measurement_basis, item.standard_unit)


class MetricStore:
    """以JSON契约为权威载荷、以关系列支持PIT筛选的小型仓库。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metric_definitions (
                    metric_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (metric_id, version)
                );
                CREATE TABLE IF NOT EXISTS metric_observations (
                    observation_id TEXT PRIMARY KEY,
                    company_id TEXT NOT NULL,
                    metric_id TEXT NOT NULL,
                    fiscal_year INTEGER NOT NULL,
                    period_kind TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    document_published_on TEXT NOT NULL,
                    observation_version INTEGER NOT NULL,
                    supersedes_observation_id TEXT,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (supersedes_observation_id) REFERENCES metric_observations(observation_id),
                    UNIQUE(company_id, metric_id, fiscal_year, period_kind, document_id, observation_version)
                );
                CREATE INDEX IF NOT EXISTS idx_observation_pit
                    ON metric_observations(company_id, metric_id, fiscal_year, document_published_on);
                CREATE TABLE IF NOT EXISTS calculation_results (
                    calculation_id TEXT PRIMARY KEY,
                    formula_id TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_corrections (
                    correction_id TEXT PRIMARY KEY,
                    original_observation_id TEXT NOT NULL,
                    corrected_observation_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def save_definitions(self, definitions: Iterable[MetricDefinition]) -> None:
        rows = list(definitions)
        try:
            with closing(self._connect()) as connection:
                connection.executemany(
                    "INSERT INTO metric_definitions(metric_id, version, payload_json) VALUES (?, ?, ?)",
                    [(item.metric_id, item.version, item.model_dump_json()) for item in rows],
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateMetricRecordError("指标定义重复，拒绝覆盖") from exc

    def save_observations(self, observations: Iterable[MetricObservation]) -> None:
        """在一个事务中保存全部观察；任一冲突则整批回滚。"""

        rows = list(observations)
        try:
            with closing(self._connect()) as connection:
                existing = {item.observation_id: item for item in rows}
                for item in rows:
                    parent_id = item.supersedes_observation_id
                    if parent_id is None:
                        continue
                    parent = existing.get(parent_id)
                    if parent is None:
                        saved = connection.execute("SELECT payload_json FROM metric_observations WHERE observation_id=?", (parent_id,)).fetchone()
                        parent = None if saved is None else MetricObservation.model_validate_json(saved[0])
                    if (parent is None or _fact_key(parent) != _fact_key(item)
                            or item.observation_version <= parent.observation_version
                            or item.document_published_on < parent.document_published_on):
                        raise MetricSourceConflictError("更正链的事实口径、版本或披露日不一致")
                connection.executemany(
                    """
                    INSERT INTO metric_observations(
                        observation_id, company_id, metric_id, fiscal_year, period_kind,
                        document_id, document_published_on, observation_version,
                        supersedes_observation_id, content_sha256, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item.observation_id,
                            item.company_id,
                            item.metric_id,
                            item.fiscal_year,
                            item.period_kind,
                            item.document_id,
                            item.document_published_on.isoformat(),
                            item.observation_version,
                            item.supersedes_observation_id,
                            item.content_sha256,
                            item.model_dump_json(),
                        )
                        for item in rows
                    ],
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateMetricRecordError("观察记录重复或版本链无效，整批拒绝") from exc

    def save_calculations(self, calculations: Iterable[CalculationResult]) -> None:
        rows = list(calculations)
        try:
            with closing(self._connect()) as connection:
                connection.executemany(
                    """
                    INSERT INTO calculation_results(calculation_id, formula_id, input_sha256, result_sha256, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (item.calculation_id, item.formula_id, item.input_sha256, item.result_sha256, item.model_dump_json())
                        for item in rows
                    ],
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateMetricRecordError("计算结果重复，拒绝覆盖") from exc

    def query_observation_as_of(
        self, company_id: str, metric_id: str, fiscal_year: int, as_of_date: date,
        *, allowed_document_ids: set[str],
        statement_scope: str = "CONSOLIDATED",
    ) -> MetricObservation | None:
        """Query a locked document set as of a validated date, without tie-breaking facts.

        Only explicit version chains may select a newer fact. Independent roots
        or incomparable periods raise a conflict, rather than choose by ID.
        Missing/filtered facts return None. Snapshot members must be supplied.
        """

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM metric_observations
                WHERE company_id = ? AND metric_id = ? AND fiscal_year = ?
                  AND document_published_on <= ?
                """,
                (company_id, metric_id, fiscal_year, as_of_date.isoformat()),
            ).fetchall()
        candidates = [MetricObservation.model_validate_json(row["payload_json"]) for row in rows]
        candidates = [item for item in candidates if item.document_id in allowed_document_ids and item.statement_scope == statement_scope]
        if not candidates:
            return None
        if len({_fact_key(item) for item in candidates}) != 1:
            raise MetricSourceConflictError("同一查询存在不可比较的事实口径")
        superseded = {item.supersedes_observation_id for item in candidates}
        heads = [item for item in candidates if item.observation_id not in superseded]
        if len(heads) != 1:
            raise MetricSourceConflictError("存在未裁决的独立来源或分叉更正")
        return heads[0]

    def load_all_observations(self) -> list[MetricObservation]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT payload_json FROM metric_observations ORDER BY observation_id").fetchall()
        return [MetricObservation.model_validate_json(row["payload_json"]) for row in rows]

    def load_all_calculations(self) -> list[CalculationResult]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT payload_json FROM calculation_results ORDER BY calculation_id").fetchall()
        return [CalculationResult.model_validate_json(row["payload_json"]) for row in rows]

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("metric_definitions", "metric_observations", "calculation_results", "manual_corrections")
            }

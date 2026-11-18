"""Execução em segundo plano, uma coleta por vez, com progresso para a UI."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .collector import (
    Cancelled,
    CollectorError,
    SnapshotData,
    collect_account,
)
from .config import normalize_username
from .db import database, save_snapshot

LOGGER = logging.getLogger("instafollowcheck")


class BusyError(Exception):
    pass


class JobNotFound(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    username: str
    status: str = "queued"
    stage: str = "queued"
    message: str = "Na fila..."
    current: int = 0
    total: int = 0
    error: str = ""
    snapshot_id: int | None = None
    account_key: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    cancel_requested: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active_id: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}

    def active(self) -> Job | None:
        with self._lock:
            if self._active_id is None:
                return None
            return self._jobs.get(self._active_id)

    def start_collect(self, username: str) -> Job:
        normalized = normalize_username(username)
        with self._lock:
            if self._active_id is not None and self._jobs.get(self._active_id):
                active = self._jobs[self._active_id]
                if active.status in {"queued", "running", "waiting_login"}:
                    raise BusyError("Já existe uma coleta em andamento.")
            job = Job(id=uuid.uuid4().hex, kind="collect", username=normalized)
            self._jobs[job.id] = job
            self._active_id = job.id
            event = threading.Event()
            self._cancel_events[job.id] = event
        thread = threading.Thread(
            target=self._run, args=(job.id, normalized, event), name="collector", daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound("Execução não encontrada.")
            return job

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound("Execução não encontrada.")
            job.cancel_requested = True
            job.message = "Cancelando..."
            event = self._cancel_events.get(job_id)
            if event:
                event.set()
            return job

    def _update(self, job: Job, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    def _progress(self, job: Job, stage: str, message: str, current: int, total: int) -> None:
        status = "waiting_login" if stage == "waiting_login" else "running"
        self._update(
            job,
            stage=stage,
            message=message,
            current=current,
            total=total,
            status=status,
        )

    def _run(self, job_id: str, username: str, cancel_event: threading.Event) -> None:
        job = self.get(job_id)
        self._update(job, status="running", stage="starting", message="Abrindo o navegador...")
        try:
            snapshot = collect_account(
                self.data_dir,
                username,
                progress=lambda stage, message, current, total: self._progress(
                    job, stage, message, current, total
                ),
                cancel=cancel_event.is_set,
            )
            self._update(job, stage="saving", message="Salvando snapshot...")
            snapshot_id, account_key = self._save(snapshot)
            LOGGER.info(
                "Coleta de @%s concluída: %d seguidores, %d seguindo.",
                username,
                len(snapshot.followers),
                len(snapshot.following),
            )
            self._update(
                job,
                status="success",
                stage="done",
                message="Coleta concluída.",
                current=len(snapshot.followers) + len(snapshot.following),
                total=len(snapshot.followers) + len(snapshot.following),
                snapshot_id=snapshot_id,
                account_key=account_key,
            )
        except Cancelled:
            self._update(job, status="cancelled", stage="cancelled", message="Coleta cancelada.")
        except CollectorError as exc:
            LOGGER.warning("Coleta de @%s falhou: %s", username, exc)
            self._update(job, status="error", stage="error", message=str(exc), error=str(exc))
        except Exception:
            LOGGER.exception("Falha inesperada na coleta de @%s", username)
            message = "Falha inesperada na coleta. Nada foi salvo; tente novamente."
            self._update(job, status="error", stage="error", message=message, error=message)
        finally:
            with self._lock:
                if self._active_id == job_id:
                    self._active_id = None
                self._cancel_events.pop(job_id, None)

    def _save(self, snapshot: SnapshotData) -> tuple[int, str]:
        owner_key = f"id:{snapshot.owner_id}"
        with database(self.data_dir) as connection:
            snapshot_id = save_snapshot(
                connection,
                owner_key=owner_key,
                owner_id=snapshot.owner_id,
                username=snapshot.username,
                captured_at=snapshot.captured_at,
                source="browser",
                followers=snapshot.followers,
                following=snapshot.following,
                reported_followers=snapshot.reported_followers,
                reported_following=snapshot.reported_following,
                note=snapshot.note,
            )
        return snapshot_id, owner_key

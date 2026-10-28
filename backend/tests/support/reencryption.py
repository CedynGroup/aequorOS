from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.key_management import reencryption
from app.db.base import utc_now
from app.models.job import Job
from app.services import job_queue
from app.storage.client import StorageClient


def drain(db: Session, job: Job, storage: StorageClient) -> Job:
    for _ in range(10000):
        progress = reencryption.Progress.model_validate(job.progress)
        if progress.next_job_id is not None:
            following = db.get(Job, progress.next_job_id)
            assert following is not None
            job = following
            continue
        if progress.phase == "done":
            return job
        job.status = "running"
        job.started_at = utc_now()
        db.commit()
        reencryption.run(db, job, storage)
        job_queue.complete(db, job)
    raise AssertionError("Re-encryption did not finish its bounded dispatches.")

"""Initialize public inventory without probing a host or inventing current observations."""
import yaml
from sqlalchemy import select

from srs.config import Settings
from srs.models import Node, Project


def seed(session, settings: Settings) -> None:
    raw = yaml.safe_load(settings.inventory_file.read_text())
    for item in raw.get("nodes", []):
        item = dict(item)
        item["id"] = str(item["id"])
        existing = session.get(Node, item["id"])
        if existing is not None and existing.facts.get("deleted_at"):
            continue
        if existing is None:
            session.add(Node(**item, facts={"source": "read-only inventory 2026-11-06", "fresh": False}))
        elif any(getattr(existing, key) != value for key, value in item.items() if key not in {"id", "inventory_revision"}):
            for key, value in item.items():
                if key not in {"id", "inventory_revision"}:
                    setattr(existing, key, value)
            existing.inventory_revision += 1
            existing.snapshot, existing.observed_at = None, None
            existing.facts = {"source": "operator-updated public inventory", "fresh": False}
    if session.scalar(select(Project.id).limit(1)) is None:
        session.add(Project(
            id="00000000-0000-4000-8000-000000000001",
            name="合成訓練驗證",
            path="/srv/srs/projects/synthetic",
            entrypoint="workloads/train.py",
            modes=["cpu", "single", "ddp", "fsdp2"],
            container="/srv/srs/images/pytorch-2.14.1-cu126.sif",
            revision="demo-template-v1" if settings.mode == "demo" else "requires-verified-manifest",
            owner=settings.web_admin_username,
        ))
    session.commit()

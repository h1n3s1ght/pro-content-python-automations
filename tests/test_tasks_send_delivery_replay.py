import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")

import app.tasks as tasks_module


def test_send_delivery_claim_statuses_include_sent_only_for_replay(monkeypatch):
    seen_allowed_statuses: list[tuple[str, ...]] = []

    def _fake_claim(_delivery_id: str, *, allowed_statuses=None):
        seen_allowed_statuses.append(tuple(allowed_statuses or ()))
        return None

    monkeypatch.setattr(tasks_module, "claim_delivery", _fake_claim)

    tasks_module.send_delivery.run("delivery-no-replay", tier="pro", replay=False)
    tasks_module.send_delivery.run("delivery-replay", tier="pro", replay=True)

    assert seen_allowed_statuses[0] == tasks_module.READY_STATUSES
    assert "SENT" not in seen_allowed_statuses[0]
    assert seen_allowed_statuses[1] == (*tasks_module.READY_STATUSES, "SENT")

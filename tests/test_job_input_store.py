import uuid

import app.job_input_store as store


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, execute_values):
        self._execute_values = list(execute_values)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, _stmt):
        value = self._execute_values.pop(0)
        return _ScalarResult(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_upsert_job_input_returns_id_and_commits(monkeypatch):
    returned_id = uuid.uuid4()
    fake_session = _FakeSession([returned_id])

    monkeypatch.setattr(store, "get_sessionmaker", lambda: (lambda: fake_session))

    payload = {"metadata": {"business_name": "Acme", "business_domain": "acme.com"}}
    result = store.upsert_job_input(job_id="job-1", input_payload=payload)

    assert result == returned_id
    assert fake_session.commits == 1
    assert fake_session.rollbacks == 0
    assert fake_session.closed is True


def test_get_job_input_payload_returns_dict(monkeypatch):
    fake_session = _FakeSession([{"metadata": {"business_name": "Acme"}}])
    monkeypatch.setattr(store, "get_sessionmaker", lambda: (lambda: fake_session))

    result = store.get_job_input_payload("job-1")

    assert result == {"metadata": {"business_name": "Acme"}}
    assert fake_session.closed is True


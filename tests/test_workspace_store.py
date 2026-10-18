"""Incident document store: checked versions, durable writes, one winner between concurrent writers."""
from concurrent.futures import ThreadPoolExecutor

from lastmile.workspace import WorkspaceError, WorkspaceStore


def test_persistence_versions_and_concurrent_writers(tmp_path):
    path = tmp_path / "response.sqlite3"
    first = WorkspaceStore(path)
    doc = first.create("An incident")
    second = WorkspaceStore(path)
    assert second.get(doc["id"]) == doc

    def write(store):
        try:
            store.mutate(doc["id"], 1, "Reviewer", lambda d: d.update(description="Reviewed"))
            return "saved"
        except WorkspaceError:
            return "stale"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, [first, second])) == ["saved", "stale"]
    first.close()
    second.close()
    restored = WorkspaceStore(path)
    assert restored.get(doc["id"])["version"] == 2
    assert restored.get(doc["id"])["description"] == "Reviewed"
    restored.close()

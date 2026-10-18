"""Cloud adapter contracts with generation-aware in-memory object storage."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("google.cloud.storage")
from google.api_core.exceptions import NotFound, PreconditionFailed

from lastmile.cloud_storage import CloudAlertStore, CloudFiles, CloudSubscriptionStore, CloudWorkspaceStore
from lastmile.workspace import WorkspaceError


class Bucket:
    def __init__(self):
        self.data = {}
        self.serial = 0
        self.before_write = None

    def blob(self, name):
        bucket = self
        class Blob:
            generation = None
            def __init__(self):
                self.name = name
            def reload(self, **kw):
                if name not in bucket.data or (self.generation is not None and bucket.data[name][0] != self.generation):
                    raise NotFound(name)  # like Cloud Storage: a handle that knows a generation reloads that generation
                self.generation = bucket.data[name][0]
            def download_as_text(self, if_generation_match=None, **kw):
                self.reload()
                if if_generation_match is not None and self.generation != if_generation_match:
                    raise PreconditionFailed(name)
                return bucket.data[name][1].decode()
            def upload_from_string(self, value, if_generation_match=None, **kw):
                if bucket.before_write:
                    callback, bucket.before_write = bucket.before_write, None
                    callback()
                actual = bucket.data.get(name, (0, None))[0]
                if if_generation_match is not None and actual != if_generation_match:
                    raise PreconditionFailed(name)
                bucket.serial += 1
                bucket.data[name] = bucket.serial, value.encode() if isinstance(value, str) else value
                self.generation = bucket.serial
            def download_to_filename(self, target, **kw):
                Path(target).write_bytes(bucket.data[name][1])
            def upload_from_filename(self, path, **kw):
                self.upload_from_string(Path(path).read_bytes(), **kw)
        return Blob()

    def list_blobs(self, prefix, **kw):
        result = []
        for key in self.data:
            if key.startswith(prefix):
                blob = self.blob(key)
                blob.reload()
                result.append(blob)
        if getattr(self, "after_list", None):
            callback, self.after_list = self.after_list, None
            callback()
        return result


def test_private_registry_and_team_documents_survive_cloud_restore(tmp_path):
    from lastmile.team_accounts import Accounts, REGISTRY
    from lastmile.team_workspace import TeamWorkspace
    bucket = Bucket()
    one = CloudWorkspaceStore(CloudFiles("private", tmp_path / "a", bucket))
    account = Accounts(one)
    user = account.bootstrap("admin", "Test admin", "exercise-password-123")
    token, _ = account.login("admin", "exercise-password-123")
    doc = TeamWorkspace(one).create(user, "Fictional team case", "Nuwara Eliya", ["division"])
    two = CloudWorkspaceStore(CloudFiles("private", tmp_path / "b", bucket))
    restored = Accounts(two)
    assert restored.current(token)["id"] == user["id"]
    assert two.list() == []
    assert [d["id"] for d in two.team_documents()] == [doc["id"]]
    assert two.get(REGISTRY)["users"][0]["username"] == "admin"


def test_incidents_restore_and_compare_object_generation(tmp_path):
    bucket = Bucket()
    first = CloudWorkspaceStore(CloudFiles("private", tmp_path / "one", bucket))
    doc = first.create("Cloud exercise")
    second = CloudWorkspaceStore(CloudFiles("private", tmp_path / "two", bucket))
    assert second.get(doc["id"]) == doc and second.list()[0]["id"] == doc["id"]
    updated = second.mutate(doc["id"], 1, "Reviewer", lambda d: d.update(description="Updated"))
    with pytest.raises(WorkspaceError, match="changed"):
        first.mutate(doc["id"], 1, "Reviewer", lambda d: None)
    bucket.before_write = lambda: second.mutate(doc["id"], 2, "Other reviewer", lambda d: d.update(description="Concurrent"))
    with pytest.raises(WorkspaceError, match="Another reviewer"):
        first.mutate(doc["id"], 2, "Reviewer", lambda d: d.update(description="Lost write"))
    assert first.get(doc["id"])["description"] == "Concurrent"
    with pytest.raises(WorkspaceError) as missing:
        first.get("../state/secret.key")
    assert missing.value.status == 404


def test_warning_history_and_subscribers_survive_replacement(tmp_path, clean_run):
    bucket = Bucket()
    first = CloudFiles("private", tmp_path / "one", bucket)
    runs = CloudAlertStore(first)
    runs.put(clean_run)
    runs.write_outbox(clean_run.id, "test.txt", "Exercise artifact")
    subs = CloudSubscriptionStore(first)
    person, token = subs.subscribe("exercise@example.com", "lk", ["LK-BD"], "en", 1, False)
    subs.confirm(token)
    second = CloudFiles("private", tmp_path / "replacement", bucket)
    assert CloudAlertStore(second).get(clean_run.id).id == clean_run.id
    restored = CloudSubscriptionStore(second)
    assert restored.by_email("exercise@example.com").confirmed
    assert restored._secret == subs._secret
    assert "state/outbox/" + clean_run.id + "/test.txt" in bucket.data


def test_cloud_restore_refuses_path_escape(tmp_path):
    bucket = Bucket()
    bucket.blob("state/runs/../../outside.json").upload_from_string("{}")
    with pytest.raises(ValueError, match="invalid file path"):
        CloudFiles("private", tmp_path / "data", bucket).restore("runs/")


def test_conversation_metadata_preserves_domain_version_and_generation_cas(tmp_path):
    bucket = Bucket()
    first = CloudWorkspaceStore(CloudFiles("private", tmp_path / "first", bucket))
    second = CloudWorkspaceStore(CloudFiles("private", tmp_path / "second", bucket))
    doc = first.create("Conversation exercise")
    first.mutate(doc["id"], 1, "Reviewer", lambda d: d.update(conversations={"main": {"messages": ["Saved chat"]}}), metadata=True)
    restored = second.get(doc["id"])
    assert restored["version"] == 1 and restored["conversations"]["main"]["messages"] == ["Saved chat"]
    bucket.before_write = lambda: second.mutate(doc["id"], 1, "Reviewer", lambda d: d.update(description="Concurrent domain change"))
    with pytest.raises(WorkspaceError, match="Another reviewer"):
        first.mutate(doc["id"], 1, "Reviewer", lambda d: d.update(conversations={}), metadata=True)
    assert second.get(doc["id"])["conversations"] == restored["conversations"]


def test_incidents_saved_or_deleted_while_listing_still_list(tmp_path):
    """Cloud Run regression: a chat save between listing and reading used to 404 the whole page."""
    bucket = Bucket()
    store = CloudWorkspaceStore(CloudFiles("private", tmp_path / "one", bucket))
    kept, gone = store.create("Kept exercise"), store.create("Removed exercise")
    bucket.after_list = lambda: (store.mutate(kept["id"], 1, "Reviewer", lambda d: d.update(description="Saved meanwhile")),
                                 bucket.data.pop("incidents/" + gone["id"] + ".json"))
    listed = store.list()
    assert [d["id"] for d in listed] == [kept["id"]] and listed[0]["version"] == 2


def test_an_incident_saved_while_it_is_read_is_read_again(tmp_path):
    """Cloud Run regression: a background save between reload and download used to answer "Incident not found"."""
    bucket = Bucket()
    store = CloudWorkspaceStore(CloudFiles("private", tmp_path / "one", bucket))
    doc = store.create("Busy exercise")
    original = bucket.blob
    def racing(name):
        blob = original(name)
        download = blob.download_as_text
        def once(if_generation_match=None, **kw):
            if not getattr(bucket, "raced", False):
                bucket.raced = True
                store.mutate(doc["id"], 1, "Background", lambda d: d.update(description="Saved meanwhile"), metadata=True)
            return download(if_generation_match=if_generation_match, **kw)
        blob.download_as_text = once
        return blob
    bucket.blob = racing
    assert store.get(doc["id"])["description"] == "Saved meanwhile"
    with pytest.raises(WorkspaceError, match="not found"):
        store.get("incident_000000000000")


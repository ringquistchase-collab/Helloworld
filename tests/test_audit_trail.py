import json
import os

from audit_trail import AuditTrail


def test_log_appends_entry(tmp_path):
    audit = AuditTrail(os.path.join(str(tmp_path), "audit.jsonl"))
    audit.log(module="test", action="did_thing", node_id="n1", details={"x": 1})
    entries = audit.read_all()
    assert len(entries) == 1
    assert entries[0]["module"] == "test"
    assert entries[0]["action"] == "did_thing"


def test_verify_chain_true_when_untouched(tmp_path):
    audit = AuditTrail(os.path.join(str(tmp_path), "audit.jsonl"))
    audit.log(module="a", action="one", node_id="n1", details={})
    audit.log(module="b", action="two", node_id="n1", details={})
    ok, bad = audit.verify_chain()
    assert ok is True
    assert bad == []


def test_verify_chain_catches_tampered_entry(tmp_path):
    path = os.path.join(str(tmp_path), "audit.jsonl")
    audit = AuditTrail(path)
    audit.log(module="a", action="one", node_id="n1", details={"real": True})
    audit.log(module="b", action="two", node_id="n1", details={})

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    tampered = json.loads(lines[0])
    tampered["details"] = {"real": False}
    lines[0] = json.dumps(tampered) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    ok, bad = AuditTrail(path).verify_chain()
    assert ok is False
    assert len(bad) >= 1


def test_chain_continues_across_reopen(tmp_path):
    path = os.path.join(str(tmp_path), "audit.jsonl")
    AuditTrail(path).log(module="a", action="one", node_id="n1", details={})
    AuditTrail(path).log(module="b", action="two", node_id="n1", details={})
    ok, bad = AuditTrail(path).verify_chain()
    assert ok is True

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from bworkflow_sql.public_contracts import (
    build_workflow_observation,
    build_workflow_observation_error,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "bworkflow-observation.v1.schema.json"
EXAMPLES = ROOT / "contracts" / "examples"
EXAMPLE_PATHS = {
    "ready": EXAMPLES / "workflow-doctor.ready.v1.json",
    "blocked": EXAMPLES / "workflow-doctor.blocked.v1.json",
    "failed": EXAMPLES / "workflow-doctor.failed.v1.json",
}
UNSAFE_NESTED_KEYS = [
    "next",
    "Command",
    "followUpCommand",
    "argv",
    "cwd",
    "traceback",
    "stdout",
    "stderr",
    "environment",
    "env",
    "api_key",
    "apiKey",
    "access_token",
    "AccessToken",
    "githubToken",
    "clientSecret",
    "Authorization",
    "Cookie",
    "password",
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validator() -> Draft202012Validator:
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def example(status: str) -> dict:
    return load_json(EXAMPLE_PATHS[status])


def assert_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validator().validate(payload)


def test_schema_is_valid_and_all_examples_conform():
    contract = validator()

    for status, path in EXAMPLE_PATHS.items():
        payload = load_json(path)
        contract.validate(payload)
        assert payload["status"] == status


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "WorkflowObservation"),
        ("schema_version", 2),
        ("authoritative", True),
    ],
)
def test_contract_identity_is_fixed(field: str, value: object):
    payload = example("ready")
    payload[field] = value

    assert_invalid(payload)


@pytest.mark.parametrize(
    ("status", "change"),
    [
        ("ready", {"ok": False}),
        ("ready", {"blocked_by": ["voice_and_assembly"]}),
        ("ready", {"error": {"code": "x", "message": "x", "retryable": False, "owner": "bworkflow"}}),
        ("blocked", {"ok": False}),
        ("blocked", {"blocked_by": []}),
        ("blocked", {"error": {"code": "x", "message": "x", "retryable": False, "owner": "bworkflow"}}),
        ("failed", {"ok": True}),
        ("failed", {"blocked_by": ["process"]}),
        ("failed", {"checks": {"script": {"ok": False}}}),
        ("failed", {"issues": [{"code": "internal"}]}),
        ("failed", {"suggestion": {"action": "retry", "task": None}}),
        ("failed", {"error": None}),
    ],
)
def test_state_matrix_rejects_invalid_field_combinations(status: str, change: dict):
    payload = example(status)
    payload.update(change)

    assert_invalid(payload)


@pytest.mark.parametrize("status", ["ready", "blocked", "failed"])
def test_subject_is_always_a_fixed_object_with_nullable_values(status: str):
    payload = example(status)
    payload["subject"] = {"project_id": None, "project_name": None, "account": None}
    validator().validate(payload)

    missing_field = deepcopy(payload)
    missing_field["subject"].pop("account")
    assert_invalid(missing_field)

    extra_field = deepcopy(payload)
    extra_field["subject"]["scheme_name"] = "main"
    assert_invalid(extra_field)

    null_subject = deepcopy(payload)
    null_subject["subject"] = None
    assert_invalid(null_subject)


@pytest.mark.parametrize(
    "field",
    ["command", "argv", "traceback", "api_key", "access_token", "password"],
)
def test_private_executable_and_secret_top_level_fields_are_rejected(field: str):
    payload = example("ready")
    payload[field] = "private"

    assert_invalid(payload)


@pytest.mark.parametrize("unsafe_key", UNSAFE_NESTED_KEYS)
def test_nested_private_and_secret_keys_are_rejected(unsafe_key: str):
    payload = example("ready")
    payload["checks"] = {"script": {"detail": {unsafe_key: "private"}}}

    assert_invalid(payload)


def test_checks_accept_unknown_safe_nested_diagnostics():
    payload = example("ready")
    payload["checks"] = {
        "future_check": {
            "new_metric": 12.5,
            "token_count": 128,
            "secretary": "safe diagnostic label",
            "flags": [True, False],
            "detail": {"owner_note": "safe"},
        }
    }

    validator().validate(payload)


@pytest.mark.parametrize(
    "code",
    [
        "project_not_found",
        "ambiguous_project_reference",
        "workflow_doctor_internal_error",
    ],
)
def test_failed_diagnostics_share_one_complete_shape(code: str):
    payload = example("failed")
    payload["error"]["code"] = code

    validator().validate(payload)
    assert payload["subject"] == {"project_id": None, "project_name": None, "account": None}
    assert payload["blocked_by"] == []
    assert payload["checks"] == {}
    assert payload["issues"] == []
    assert payload["suggestion"] is None


def test_builder_maps_ready_result_to_valid_non_authoritative_observation():
    payload = build_workflow_observation(
        {
            "ok": True,
            "status": "ready",
            "blocked_by": None,
            "project": {"id": 23, "name": "数码-桌面音响"},
            "account": "小博",
            "checks": {"script": {"status": "ready_for_downstream"}},
            "issues": [],
            "next": {"action": "assemble", "task": "生成成片", "command": "private"},
        }
    )

    validator().validate(payload)
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["subject"] == {
        "project_id": 23,
        "project_name": "数码-桌面音响",
        "account": "小博",
    }
    assert payload["blocked_by"] == []
    assert payload["suggestion"] == {"action": "assemble", "task": "生成成片"}
    assert "command" not in payload["suggestion"]


@pytest.mark.parametrize(
    ("raw_blockers", "expected"),
    [
        ("voice_and_assembly", ["voice_and_assembly"]),
        (["voice_and_assembly", "template", "voice_and_assembly"], ["voice_and_assembly", "template"]),
    ],
)
def test_builder_treats_internal_blocked_result_as_successful_observation(raw_blockers, expected):
    payload = build_workflow_observation(
        {
            "ok": False,
            "status": "blocked",
            "blocked_by": raw_blockers,
            "project": {"id": 23, "name": "数码-桌面音响"},
            "account": "小博",
            "checks": {},
            "issues": [],
            "next": {"action": "generate_voice"},
        }
    )

    validator().validate(payload)
    assert payload["ok"] is True
    assert payload["status"] == "blocked"
    assert payload["blocked_by"] == expected
    assert payload["error"] is None


def test_builder_recursively_removes_private_and_secret_diagnostics():
    payload = build_workflow_observation(
        {
            "ok": True,
            "status": "ready",
            "project": {"id": 23, "name": "数码-桌面音响"},
            "account": "小博",
            "checks": {
                "script": {
                    "status": "ready",
                    "next": {"action": "private"},
                    "detail": {
                        "command": "private",
                        "cwd": "private",
                        "traceback": "private",
                        "environment": {"HOME": "private"},
                        "clientSecret": "secret",
                        "safe_note": "keep",
                    },
                }
            },
            "issues": [
                {
                    "code": "safe_issue",
                    "detail": {"argv": ["private"], "stderr": "private", "AccessToken": "secret", "count": 1},
                }
            ],
            "next": {
                "action": "assemble",
                "task": "生成成片",
                "command": "private",
                "follow_up_command": "private",
                "access_token": "secret",
            },
        }
    )

    validator().validate(payload)
    assert payload["checks"] == {
        "script": {"status": "ready", "detail": {"safe_note": "keep"}}
    }
    assert payload["issues"] == [{"code": "safe_issue", "detail": {"count": 1}}]
    assert payload["suggestion"] == {"action": "assemble", "task": "生成成片"}


@pytest.mark.parametrize("unsafe_key", UNSAFE_NESTED_KEYS)
def test_builder_uses_the_same_unsafe_key_policy_as_the_schema(unsafe_key: str):
    payload = build_workflow_observation(
        {
            "ok": True,
            "status": "ready",
            "project": {"id": 23, "name": "数码-桌面音响"},
            "account": "小博",
            "checks": {"detail": {unsafe_key: "private", "safe_note": "keep"}},
            "issues": [],
            "next": {},
        }
    )

    validator().validate(payload)
    assert payload["checks"] == {"detail": {"safe_note": "keep"}}


@pytest.mark.parametrize(
    "internal_result",
    [
        {"status": "blocked", "blocked_by": [{"access_token": "secret"}]},
        {"status": "ready", "next": {"action": {"api_key": "secret"}}},
        {"status": "ready", "project": {"name": ["not", "text"]}},
    ],
)
def test_builder_rejects_container_type_confusion_in_scalar_fields(internal_result: dict):
    with pytest.raises(TypeError):
        build_workflow_observation(internal_result)


def test_builder_rejects_unknown_internal_status_instead_of_guessing():
    with pytest.raises(ValueError, match="unsupported workflow observation status"):
        build_workflow_observation({"ok": False, "status": "crashed"})


def test_error_builder_emits_complete_valid_failed_shape_without_inventing_subject():
    payload = build_workflow_observation_error(
        "workflow_doctor_internal_error",
        "Workflow diagnosis failed.",
        retryable=True,
    )

    validator().validate(payload)
    assert payload == {
        "kind": "BWorkflowObservation",
        "schema_version": 1,
        "capability": "workflow_doctor",
        "authoritative": False,
        "ok": False,
        "status": "failed",
        "subject": {"project_id": None, "project_name": None, "account": None},
        "blocked_by": [],
        "checks": {},
        "issues": [],
        "suggestion": None,
        "error": {
            "code": "workflow_doctor_internal_error",
            "message": "Workflow diagnosis failed.",
            "retryable": True,
            "owner": "bworkflow",
        },
    }


def test_error_builder_normalizes_explicit_subject_and_does_not_accept_private_details():
    payload = build_workflow_observation_error(
        "ambiguous_project_reference",
        "Project reference is ambiguous.",
        subject={"project_id": "23", "project_name": " 数码-桌面音响 ", "account": ""},
    )

    validator().validate(payload)
    assert payload["subject"] == {
        "project_id": 23,
        "project_name": "数码-桌面音响",
        "account": None,
    }
    with pytest.raises(TypeError):
        build_workflow_observation_error(
            "unsafe",
            "unsafe",
            traceback="private",
            environment={"TOKEN": "private"},
        )


def test_error_builder_requires_a_real_boolean_retryable_flag():
    with pytest.raises(TypeError, match="retryable must be a boolean"):
        build_workflow_observation_error(
            "workflow_doctor_internal_error",
            "Workflow diagnosis failed.",
            retryable="false",
        )


@pytest.mark.parametrize(
    "unsafe_code",
    ["SECRET INTERNAL DETAIL", "bad\ncode", "C:/private/traceback.txt", "UPPER_CASE", "_leading"],
)
def test_error_code_is_a_stable_machine_identifier(unsafe_code: str):
    payload = example("failed")
    payload["error"]["code"] = unsafe_code

    assert_invalid(payload)
    with pytest.raises(ValidationError):
        build_workflow_observation_error(unsafe_code, "Safe public message.")

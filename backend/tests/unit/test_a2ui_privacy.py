"""Browser-only surfaces are filtered before any model turn is constructed."""

import pytest
from pydantic import ValidationError

from adk.a2ui_privacy import public_surface_snapshot
from protocols.a2ui_surface_action_routes import SurfaceActionRequest
from protocols.a2ui_surface_action_run_routes import SurfaceActionRunRequest, _build_run_input


def test_action_triggered_turn_drops_private_details_from_both_input_channels():
    request = SurfaceActionRunRequest.model_validate(
        {
            "surfaceId": "board",
            "action": {"name": "refresh"},
            "forwardedProps": {
                "a2ui_surface_state": {
                    "board": {"dataModel": {"count": 1}},
                    "private:board:review": {"dataModel": {"purpose": "private-canary"}},
                }
            },
        }
    )
    run = _build_run_input("session", request)
    assert run.state["a2ui_surface_state"] == {"board": {"dataModel": {"count": 1}}}
    assert run.forwarded_props["a2ui_surface_state"] == run.state["a2ui_surface_state"]
    assert "private-canary" not in run.model_dump_json()


@pytest.mark.parametrize("schema", [SurfaceActionRequest, SurfaceActionRunRequest])
def test_display_only_surface_cannot_initiate_any_server_action(schema):
    with pytest.raises(ValidationError, match="display-only"):
        schema.model_validate({"surfaceId": "private:board:review", "action": {"name": "approve"}})


def test_snapshot_filter_fails_closed_for_malformed_payloads():
    assert public_surface_snapshot(None) == {}
    assert public_surface_snapshot(["private:board:review"]) == {}
    assert public_surface_snapshot({4: "not a surface", "private:board:review": "canary"}) == {}

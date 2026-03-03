"""Tests for the JSON debug protocol response builders."""

from debug_common import error_response, completed_response, DebugResponseMixin


RESPONSE_KEYS = {
    "status", "command", "message", "current_location",
    "call_stack", "local_variables", "stdout_new", "stderr_new",
}


class TestErrorResponse:
    def test_basic_error(self):
        resp = error_response("something broke")
        assert resp["status"] == "error"
        assert resp["message"] == "something broke"
        assert resp["command"] == ""
        assert set(resp.keys()) == RESPONSE_KEYS

    def test_error_with_command(self):
        resp = error_response("bad arg", command="step_in")
        assert resp["command"] == "step_in"
        assert resp["status"] == "error"

    def test_error_defaults(self):
        resp = error_response("x")
        assert resp["current_location"] is None
        assert resp["call_stack"] == []
        assert resp["local_variables"] == {}
        assert resp["stdout_new"] == ""
        assert resp["stderr_new"] == ""


class TestCompletedResponse:
    def test_basic_completed(self):
        resp = completed_response("done stepping")
        assert resp["status"] == "completed"
        assert resp["message"] == "done stepping"
        assert resp["command"] == ""
        assert set(resp.keys()) == RESPONSE_KEYS

    def test_completed_with_stdout(self):
        resp = completed_response("ok", stdout="hello world")
        assert resp["stdout_new"] == "hello world"
        assert resp["stderr_new"] == ""

    def test_completed_with_command(self):
        resp = completed_response("ok", command="continue")
        assert resp["command"] == "continue"


class TestDebugResponseMixin:
    def test_mixin_error(self):
        obj = DebugResponseMixin()
        resp = obj._error("mixin error")
        assert resp["status"] == "error"
        assert resp["message"] == "mixin error"

    def test_mixin_completed(self):
        obj = DebugResponseMixin()
        resp = obj._completed("mixin ok", stdout="output")
        assert resp["status"] == "completed"
        assert resp["stdout_new"] == "output"

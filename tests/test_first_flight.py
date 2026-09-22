from aeris.core.enums import ExecutionState
from examples.first_flight import main


async def test_first_flight_diverts_from_tool_a_to_tool_b(capsys):
    await main()
    captured = capsys.readouterr().out
    assert '"state": "COMPLETED"' in captured
    assert '"route": "bravo"' in captured
    assert "tool_a" in captured
    assert "tool_b" in captured
    assert ExecutionState.COMPLETED.value in captured

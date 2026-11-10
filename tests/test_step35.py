"""Initializer must carry completion waiting into the simulator's instructions."""
import pytest
from helpers.verifier_cases import candidate
from pioneer.initializer.requirements import WAIT_FOR_COMPLETION, require_completion_wait


def test_initializer_must_include_completion_wait():
    task, *_ = candidate()
    with pytest.raises(ValueError, match="wait-for-completion"):
        require_completion_wait(task)
    task.user_scenario.instructions.task_instructions += " " + WAIT_FOR_COMPLETION
    require_completion_wait(task)

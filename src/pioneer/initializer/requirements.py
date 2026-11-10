"""[INDEPENDENT] Required simulator instruction, motivated by real E8 failures."""

WAIT_FOR_COMPLETION = "Wait until the agent confirms completion of your request before ending the conversation."


def require_completion_wait(task):
    instructions = task.user_scenario.instructions
    text = instructions.task_instructions if hasattr(instructions, "task_instructions") else ""
    if WAIT_FOR_COMPLETION.casefold() not in text.casefold():
        raise ValueError("task_instructions must contain the required wait-for-completion sentence, "
                         f"verbatim: {WAIT_FOR_COMPLETION!r}")

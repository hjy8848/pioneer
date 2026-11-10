"""Hand-constructed verifier counterexamples, never a task synthesis entry point."""
from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.data_model.tasks import Action, Task, UserScenario, StructuredUserInstructions, EvaluationCriteria
from pioneer.env import RetailSynthEnv
from pioneer.schemas import TaskMeta, ExpectedOutcome


def text(value):
    return dict(source="text", value=value)


def read(step, path=""):
    return dict(source="read", step=step, path=path)


def candidate(status="pending", holdout=()):
    env = RetailSynthEnv()
    env.init()
    order = env.query_data("orders", [("status", "eq", status), ("user_id", "not_in", list(holdout))], limit=1)[0]
    user = env.query_data("users", [("user_id", "eq", order["user_id"])])[0]
    task = Task(id="handcrafted-cancel", user_scenario=UserScenario(instructions=StructuredUserInstructions(
        domain="retail", reason_for_call="Cancel my pending order: no longer needed.",
        known_info=f"My email is {user['email']}. My order is {order['order_id']}.",
        task_instructions="Authenticate using my email. Confirm the order and reason. Say yes when asked to confirm cancellation. After saying yes, wait for the agent to carry out cancellation and confirm the order is cancelled. Do not thank or end the conversation at the confirmation step. Only stop once cancellation is reported as completed.")),
        evaluation_criteria=EvaluationCriteria(actions=[Action(action_id="cancel", name="cancel_pending_order",
            arguments=dict(order_id=order["order_id"], reason="no longer needed"))], reward_basis=["DB"]))
    meta = TaskMeta(scenario_id="handcrafted", depth=0, expected_outcome=ExpectedOutcome.COMPLETE,
        structure_signature="", instance_signature="", num_write_actions=1,
        read_steps=[dict(tool="find_user_id_by_email", arguments=dict(email=text(user["email"]))),
                    dict(tool="get_user_details", arguments=dict(user_id=read(0))),
                    dict(tool="get_order_details", arguments=dict(order_id=text(order["order_id"])))],
        argument_bindings={"0.order_id": read(2, "order_id"), "0.reason": text("no longer needed")})
    return task, meta, order, user


def reference_episode(task, implementation=None, seed=None, *, omit_actions=False):
    env = RetailSynthEnv()
    env.init(task)
    messages = list(task.initial_state.message_history or []) if task.initial_state else []
    if not omit_actions:
        for index, action in enumerate(task.evaluation_criteria.actions):
            call = ToolCall(id=f"test_{index}", name=action.name, arguments=action.arguments)
            messages.extend([AssistantMessage(role="assistant", tool_calls=[call]), env.env.get_response(call)])
    if task.evaluation_criteria.communicate_info:
        messages.append(AssistantMessage(role="assistant", content="\n".join(task.evaluation_criteria.communicate_info)))
    return SimulationRun(id="test", task_id=task.id, start_time="test", end_time="test", duration=0,
        termination_reason=TerminationReason.USER_STOP, messages=messages)


def inform_candidate(holdout=()):
    task, meta, order, user = candidate(holdout=holdout)
    task.id = "handcrafted-inform"
    task.user_scenario.instructions.reason_for_call = "What is the current status of my order?"
    task.user_scenario.instructions.task_instructions = "Authenticate using my email; ask for the status. Thank the agent and end after receiving the answer. Do not request any changes."
    task.evaluation_criteria.actions = []
    task.evaluation_criteria.communicate_info = [order["status"]]
    task.evaluation_criteria.reward_basis = ["DB", "COMMUNICATE"]
    meta.expected_outcome = ExpectedOutcome.INFORM
    meta.num_write_actions = 0
    meta.argument_bindings = {}
    meta.communicate_bindings = [read(2, "status")]
    return task, meta, order, user

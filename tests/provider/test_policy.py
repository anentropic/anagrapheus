"""The call policy: what makes two calls the same call, and what must not."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from anagrapheus.provider import PinnedModel, SemanticCallPolicy

from .conftest import Counted, Rig, says

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.settings import ModelSettings

    from anagrapheus.timeline._canonical import Json

POLICY = SemanticCallPolicy()
PARAMS = ModelRequestParameters()


def material(
    messages: Sequence[ModelMessage],
    model_id: str,
    parameters: ModelRequestParameters,
    settings: ModelSettings | None = None,
) -> Mapping[str, Json]:
    """The default policy's material, with no settings unless a test is about them."""
    return POLICY.material(messages, model_id, parameters, settings)


def test_two_identical_calls_a_moment_apart_are_one_call():
    """
    The failure that would make pinning useless while appearing to work.

    Every user, system and tool-return part carries a ``timestamp``. Left in the key,
    each call would be a fresh draw — reuse would never happen, nothing would raise,
    and the bill would be the only symptom.
    """
    first = material([ModelRequest(parts=[UserPromptPart(content="hi")])], "m", PARAMS)
    time.sleep(0.01)
    second = material([ModelRequest(parts=[UserPromptPart(content="hi")])], "m", PARAMS)

    assert first == second


def test_provider_assigned_ids_do_not_reach_the_key():
    """
    ``tool_call_id`` pairs a call with its return, and is assigned by the provider.

    The pairing survives as the order of the parts; the id itself differs between two
    runs of the same conversation, so keying on it would make every replay a miss.
    """
    call = ModelResponse(parts=[ToolCallPart("lookup", {"q": "x"}, tool_call_id="call_aaa")])
    same = ModelResponse(parts=[ToolCallPart("lookup", {"q": "x"}, tool_call_id="call_bbb")])

    assert material([call], "m", PARAMS) == material([same], "m", PARAMS)


def test_a_tool_return_keys_on_what_came_back():
    """The content is what the next draw is conditioned on; the ids and clock are not."""
    one = ModelRequest(parts=[ToolReturnPart("lookup", "Portugal", tool_call_id="a")])
    time.sleep(0.01)
    two = ModelRequest(parts=[ToolReturnPart("lookup", "Portugal", tool_call_id="b")])
    different = ModelRequest(parts=[ToolReturnPart("lookup", "Spain", tool_call_id="a")])

    assert material([one], "m", PARAMS) == material([two], "m", PARAMS)
    assert material([one], "m", PARAMS) != material([different], "m", PARAMS)


def test_the_model_identity_is_part_of_the_call():
    """Two models asked the same question are two draws, not one."""
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]

    assert material(messages, "one", PARAMS) != material(messages, "two", PARAMS)


def test_the_available_tools_are_part_of_the_call():
    """
    What the model may call changes what it draws, so the tool set belongs in the key.

    Not a subtlety: an agent that gains a tool can answer differently to the same
    prompt, and reusing the old answer would hide that.
    """
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]
    lookup = ToolDefinition(name="lookup", parameters_json_schema={"type": "object"})

    without = material(messages, "m", ModelRequestParameters())
    with_tool = material(messages, "m", ModelRequestParameters(function_tools=[lookup]))

    assert without != with_tool


def test_the_required_output_shape_is_part_of_the_call():
    """
    A schema the model is *told* to satisfy changes what it generates, so it is in.

    This is the one inclusion that looks like it might key on the interpretation, and
    it does not: §4a forbids keying on the parsing done *after* the draw, which is
    ``interpret``'s job and never reaches the material.
    """
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]
    answer = ToolDefinition(
        name="final_answer", parameters_json_schema={"type": "object", "required": ["city"]}
    )

    plain = material(messages, "m", ModelRequestParameters())
    structured = material(
        messages, "m", ModelRequestParameters(output_mode="tool", output_tools=[answer])
    )

    assert plain != structured


def test_an_application_policy_replaces_the_default(rig: Rig):
    """
    §4c gives the canonicalization policy to the application, and it means it.

    Here a policy that keys on the model alone makes two different prompts one call —
    absurd as a policy, and exactly the point: the library does not get a vote.
    """

    class OneCallOnly:
        def material(
            self, messages: object, model_id: str, parameters: object, settings: object
        ) -> dict[str, str]:
            return {"model": model_id}

    counted = Counted(says("Lisbon"))
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger, policy=OneCallOnly()))

    agent.run_sync("where?")
    second = agent.run_sync("something else entirely?")

    assert counted.count == 1
    assert second.output == "Lisbon"


def test_an_unknown_part_kind_keeps_its_content():
    """
    The default reduces by *exclusion*, so a part type it has never seen still counts.

    An allow-list would silently drop a new kind's content from the key, making two
    different calls collide — the worse of the two failures, and the silent one.
    """
    text = material([ModelResponse(parts=[TextPart("a")])], "m", PARAMS)
    other = material([ModelResponse(parts=[TextPart("b")])], "m", PARAMS)

    assert text != other


def test_a_tool_that_keeps_its_name_but_changes_contract_is_a_different_call():
    """
    Keying tools by name alone replays an answer produced under a different contract.

    The model is sent the description and the argument schema, not just the name, so a
    tool that keeps its name and changes its shape is a different question — and the
    stale answer would be about arguments the tool no longer takes.
    """
    before = ToolDefinition(
        name="lookup",
        parameters_json_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        description="search by query",
    )
    after = ToolDefinition(
        name="lookup",
        parameters_json_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        description="fetch by row number",
    )

    assert material([], "m", ModelRequestParameters(function_tools=[before])) != material(
        [], "m", ModelRequestParameters(function_tools=[after])
    )


def test_an_output_tool_that_changes_schema_is_a_different_call():
    """The same hazard on the output side: the answer's required shape is sent too."""
    before = ToolDefinition(name="final", parameters_json_schema={"required": ["city"]})
    after = ToolDefinition(name="final", parameters_json_schema={"required": ["country"]})

    assert material([], "m", ModelRequestParameters(output_tools=[before])) != material(
        [], "m", ModelRequestParameters(output_tools=[after])
    )


def test_capabilities_beyond_the_function_tools_are_keyed():
    """
    ``native_tools`` and tool visibility change what the model may do, so they count.

    Cherry-picking the fields to include is how they were missed; the default now
    reduces the parameters by *exclusion*, the same way it reduces message parts.
    """
    plain = material([], "m", ModelRequestParameters())
    hidden = material([], "m", ModelRequestParameters(revealed_tool_names={"a"}))
    thinking = material([], "m", ModelRequestParameters(allow_image_output=True))

    assert plain != hidden
    assert plain != thinking


def test_prompt_caching_does_not_change_the_key():
    """
    A cache hit serves the same prompt for less; the model is asked nothing different.

    Reducing by exclusion keys every field pydantic-ai adds, which is the safe default,
    but ``cache`` arrived between pydantic-ai 2.42 and 2.55 and re-keyed every committed
    corpus.
    """
    plain = material([], "m", ModelRequestParameters())
    cached = material([], "m", ModelRequestParameters(cache="1h"))

    assert plain == cached


def test_tool_order_does_not_change_the_key():
    """
    The same tools declared in another order are the same capability set.

    Worth pinning: reducing by exclusion means the whole parameters object is dumped,
    and a list preserves an order the model does not care about.
    """
    a = ToolDefinition(name="a", parameters_json_schema={"type": "object"})
    b = ToolDefinition(name="b", parameters_json_schema={"type": "object"})

    assert material([], "m", ModelRequestParameters(function_tools=[a, b])) == material(
        [], "m", ModelRequestParameters(function_tools=[b, a])
    )


def test_a_setting_that_changes_the_draw_changes_the_key():
    """
    A temperature change is a different draw; replaying the old answer would be a bug.

    The open question 04 §8 left: a consumer who pinned across a settings change and got
    the earlier answer back would reasonably call that a bug in the default.
    """
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]

    cool = material(messages, "m", PARAMS, {"temperature": 0.2})
    warm = material(messages, "m", PARAMS, {"temperature": 0.9})

    assert cool != warm
    assert material(messages, "m", PARAMS, {"max_tokens": 10}) != material(messages, "m", PARAMS)


def test_a_setting_about_delivery_does_not():
    """
    Settings about how the call travels stay out of the key.

    A timeout, headers, a service tier and cache configuration change nothing about
    what is drawn, and neither does a provider's own prefixed cache setting.
    """
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]
    delivered = cast(
        "ModelSettings",
        {
            "timeout": 30,
            "extra_headers": {"x-trace": "1"},
            "service_tier": "flex",
            "cache": True,
            "anthropic_cache_messages": True,
        },
    )

    assert material(messages, "m", PARAMS, delivered) == material(messages, "m", PARAMS)


def test_no_draw_settings_keys_as_it_did_before_settings_were_keyed():
    """
    Material with nothing to say about settings has no ``settings`` entry at all.

    So every corpus recorded before settings joined the key still replays: a call made
    with no draw-changing settings builds the same key it always did.
    """
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]

    assert "settings" not in material(messages, "m", PARAMS, {"timeout": 5})
    assert "settings" not in material(messages, "m", PARAMS, {})


def test_the_model_own_settings_are_keyed_with_the_run_ones(rig: Rig):
    """
    A temperature set on the wrapped model is the same as one passed to the run.

    The key is built from the merge the draw is made with.
    """
    counted = Counted(says("Lisbon"))
    inner = FunctionModel(counted, settings={"temperature": 0.2})
    on_model = Agent(model=PinnedModel(inner, rig.ledger))
    on_run = Agent(model=PinnedModel(counted.model(), rig.ledger))

    on_model.run_sync("where?")
    on_run.run_sync("where?", model_settings={"temperature": 0.2})
    on_run.run_sync("where?", model_settings={"temperature": 0.9})

    assert counted.count == 2

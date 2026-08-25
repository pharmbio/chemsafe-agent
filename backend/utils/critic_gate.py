"""Deciding whether the critic runs — deterministically, in code.

The critic is expensive; deciding whether to invoke it must not be. This module
is the gate: pure functions over the plan file and the current turn's messages,
returning the list of reasons a run is worth reviewing. No LLM is involved.

Why not let a model decide. Asking the executor whether its work needs review is
self-assessment, and the premise of having a critic at all is that the
executor's own account of its work is what we do not trust. Asking a *separate*
model means paying a model call to decide whether to pay a model call, over a
long transcript, unreliably. So the gate keys on evidence that is observable
without judgement: unresolved steps, tool failures, missing artifacts, and —
the one that earns the critic's place in this system — a numeric exposure limit
stated without any SOP retrieval behind it.

This mirrors how the rest of the execution routine is built: structure and
accounting in code, judgement from the model. `plan_init` parses, `plan_update`
validates, `plan_finalize` reconciles; the gate decides, and only the critique
itself is a model call.

Two entry points, one per review mode:

- `evaluate()` — full-run review, used by `simple` and `follow_up`. Looks at the
  whole turn plus the finished plan run.
- `evaluate_steps()` — step-wise review, used by `complex`. Looks at one or more
  steps that just reached a terminal status, and only at the traffic that
  produced them. Scoping matters: judged against the whole turn, step 1's
  recovered failure would re-flag every later step for the rest of the run.

Both share the same message scan, so a trigger means the same thing in either
mode and the codes stay comparable across the trial.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

from backend.utils.plan_store import PlanRun


@dataclass(frozen=True)
class CriticTrigger:
    """One reason this run is worth reviewing."""

    code: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code}: {self.detail}"


# Names that appear in a python_executor call when SOP grounding actually
# happened. `sop_search` is a skill script, not a tool, so grounding is visible
# in the code the executor ran rather than in a tool name.
_SOP_EVIDENCE = re.compile(
    r"sop_search|get_sop_retriever|EnsembleSOPRetriever|sop_documents",
    re.IGNORECASE,
)

# An occupational-limit or toxicity value in play — asserted in the agent's own
# text, or printed by `python_executor`. Deliberately broad: a false positive
# costs one critic pass, a false negative ships an ungrounded exposure number,
# which is the failure this whole system exists to prevent.
_LIMIT_ASSERTION = re.compile(
    r"\b(?:oel|twa|stel|pel|tlv|rel|idlh|ld50|lc50|ec50|ic50|noael|loael|dnel|pnec|mak|aegl)\b"
    r"|\d+(?:\.\d+)?\s*(?:mg|µg|ug|ng)\s*(?:/|·|\s)\s*m\s*(?:³|3)\b"
    r"|\d+(?:\.\d+)?\s*pp[mb]\b",
    re.IGNORECASE,
)

_UNVERIFIED = re.compile(r"UNVERIFIED", re.IGNORECASE)

# A deliverable the run said it would produce.
_ARTIFACT_PROMISE = re.compile(
    r"\b(?:figure|plot|chart|graph|report|table|csv|xlsx|export|save[ds]?\s+to)\b",
    re.IGNORECASE,
)
# Evidence a file was actually written into the output scope.
_ARTIFACT_WRITE = re.compile(
    r"prepare_output_path|ensure_output_dir|savefig|to_csv|to_excel|write_text|"
    r"\.write\(|open\([^)]*['\"][wa]",
    re.IGNORECASE,
)


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _tool_calls(message: Any) -> List[dict]:
    calls = getattr(message, "tool_calls", None) or []
    return [call for call in calls if isinstance(call, dict)]


def _is_tool_message(message: Any) -> bool:
    return getattr(message, "type", None) == "tool" or hasattr(message, "tool_call_id")


def _tool_failed(message: Any) -> bool:
    """True when a tool result is a python_executor failure envelope.

    LangChain serializes a dict-returning tool into the message content, so the
    `{"ok": false, ...}` envelope arrives as a JSON string. The decode is kept
    narrow so a run that legitimately prints JSON is not misread as a failure —
    the same care `tool_display.coerce_result` takes on the UI side.
    """
    raw = _text(getattr(message, "content", None)).strip()
    if not raw.startswith("{"):
        return False
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("ok") is False


def _call_source(call: dict) -> str:
    args = call.get("args")
    if isinstance(args, dict):
        return " ".join(str(value) for value in args.values())
    return str(args or "")


# Messages the gate must not read as evidence about the run. The gate's own
# brief and the critic's verdict quote the very things the patterns look for
# ("an exposure limit was stated with no SOP retrieval"), so scanning them would
# make the previous review the reason for the next one.
_NON_EVIDENCE_AUTHORS = frozenset(
    {"critic_gate", "critic_review", "plan_init", "plan_finalize"}
)


@dataclass(frozen=True)
class _Evidence:
    """What the message scan found in one slice of the transcript."""

    failures: int = 0
    last_executor_failed: bool = False
    sop_grounded: bool = False
    wrote_artifact: bool = False
    promised_artifact: bool = False
    asserted_limit: bool = False
    flagged_unverified: bool = False


def _scan(messages: Sequence[Any]) -> _Evidence:
    failures = 0
    last_executor_failed = False
    sop_grounded = False
    wrote_artifact = False
    promised_artifact = False
    asserted_limit = False
    flagged_unverified = False

    for message in messages:
        if str(getattr(message, "name", "") or "") in _NON_EVIDENCE_AUTHORS:
            continue

        if _is_tool_message(message):
            name = str(getattr(message, "name", "") or "")
            if _tool_failed(message):
                failures += 1
                if name == "python_executor":
                    last_executor_failed = True
            elif name == "python_executor":
                last_executor_failed = False
            body = _text(getattr(message, "content", None))
            if _SOP_EVIDENCE.search(body):
                sop_grounded = True
            # A limit the run is working with usually arrives here rather than in
            # the agent's prose: it was printed, or a database returned it. The
            # scan is confined to `python_executor` output because that is a
            # value the run computed or retrieved — a `read_files` result is a
            # document being read, and every SOP and playbook in this domain is
            # full of the same words.
            if name == "python_executor" and _LIMIT_ASSERTION.search(body):
                asserted_limit = True
            continue

        for call in _tool_calls(message):
            source = _call_source(call)
            if _SOP_EVIDENCE.search(source) or _SOP_EVIDENCE.search(str(call.get("name", ""))):
                sop_grounded = True
            if _ARTIFACT_WRITE.search(source):
                wrote_artifact = True

        body = _text(getattr(message, "content", None))
        if not body:
            continue
        if _UNVERIFIED.search(body):
            flagged_unverified = True
        if _LIMIT_ASSERTION.search(body):
            asserted_limit = True
        if _ARTIFACT_PROMISE.search(body):
            promised_artifact = True

    return _Evidence(
        failures=failures,
        last_executor_failed=last_executor_failed,
        sop_grounded=sop_grounded,
        wrote_artifact=wrote_artifact,
        promised_artifact=promised_artifact,
        asserted_limit=asserted_limit,
        flagged_unverified=flagged_unverified,
    )


def _evidence_triggers(
    evidence: _Evidence, *, failure_trigger: int, scope: str
) -> List[CriticTrigger]:
    """Triggers that read the same in either mode. `scope` only words them."""
    triggers: List[CriticTrigger] = []
    where = f"in {scope}" if scope else "in this run"

    if evidence.failures >= failure_trigger:
        triggers.append(
            CriticTrigger("tool_failures", f"{evidence.failures} failed tool calls {where}")
        )
    elif evidence.last_executor_failed:
        triggers.append(
            CriticTrigger(
                "trailing_failure", f"the last python_executor call {where} failed"
            )
        )

    if evidence.flagged_unverified:
        triggers.append(
            CriticTrigger("unverified_marker", f"an UNVERIFIED flag was emitted {where}")
        )

    # The one that matters most in this domain, and the reason the critic is not
    # exempted on `simple`: "what is the OEL for toluene?" routes as a simple
    # one-step run, and an ungrounded number there is exactly the dangerous case.
    if evidence.asserted_limit and not evidence.sop_grounded:
        triggers.append(
            CriticTrigger(
                "ungrounded_limit",
                f"an exposure limit or toxicity value was stated {where} with no SOP retrieval",
            )
        )

    if evidence.promised_artifact and not evidence.wrote_artifact:
        triggers.append(
            CriticTrigger(
                "missing_artifact",
                f"a figure, table or report was described {where} but no file was written",
            )
        )

    return triggers


def _constraint_trigger(
    approval_constraints: Iterable[str],
) -> Optional[CriticTrigger]:
    # A conditional approval is precisely the case where a human attached a
    # requirement that can be silently dropped somewhere in the middle.
    conditions = [str(item).strip() for item in approval_constraints if str(item).strip()]
    if not conditions:
        return None
    return CriticTrigger(
        "conditional_approval",
        f"{len(conditions)} approval condition(s) to verify were honoured",
    )


def evaluate(
    *,
    run: Optional[PlanRun],
    task_category: str,
    turn_messages: Sequence[Any],
    approval_constraints: Iterable[str] = (),
    min_steps: int = 4,
    failure_trigger: int = 2,
) -> List[CriticTrigger]:
    """Reasons a finished run should be reviewed. Empty list means skip.

    Full-run mode: `simple` and `follow_up` always, and `complex` when step-wise
    review is switched off.
    """
    triggers: List[CriticTrigger] = []

    # --- Signals from the plan file -----------------------------------------
    if run is not None and run.steps:
        blocked = [s for s in run.steps if s.status == "blocked"]
        unresolved = [s for s in run.steps if not s.is_terminal]
        if blocked:
            listed = ", ".join(str(s.number) for s in blocked[:6])
            triggers.append(
                CriticTrigger("blocked_step", f"steps marked blocked: {listed}")
            )
        if unresolved:
            listed = ", ".join(str(s.number) for s in unresolved[:6])
            triggers.append(
                CriticTrigger(
                    "unresolved_step",
                    f"execution ended with steps unresolved: {listed}",
                )
            )
        if task_category == "complex" and len(run.steps) >= min_steps:
            triggers.append(
                CriticTrigger(
                    "long_run",
                    f"{len(run.steps)}-step plan; long runs drift without a check",
                )
            )

    constraint = _constraint_trigger(approval_constraints)
    if constraint is not None:
        triggers.append(constraint)

    # --- Signals from this turn's traffic -----------------------------------
    triggers.extend(
        _evidence_triggers(
            _scan(turn_messages), failure_trigger=failure_trigger, scope="this run"
        )
    )
    return triggers


def evaluate_steps(
    *,
    run: Optional[PlanRun],
    step_numbers: Sequence[int],
    step_messages: Sequence[Any],
    approval_constraints: Iterable[str] = (),
    failure_trigger: int = 2,
    always: bool = True,
) -> List[CriticTrigger]:
    """Reasons the steps that just resolved should be reviewed.

    `step_messages` is the traffic since the previous review, not the whole
    turn — the executor hands back one step at a time, so that slice is what
    produced these steps and nothing else.

    With `always` set the list is never empty for a resolved step: on a plan a
    human approved, each step is checked as it lands. With `always` off the
    same evidence triggers decide, which is the cheaper configuration.
    """
    if not step_numbers:
        return []

    triggers: List[CriticTrigger] = []
    listed = ", ".join(str(number) for number in step_numbers)
    scope = f"step {listed}" if len(step_numbers) == 1 else f"steps {listed}"

    # How the executor left each step is a signal in its own right: a step it
    # abandoned or waved through is exactly the kind the plan file will
    # otherwise carry as settled.
    if run is not None:
        for number in step_numbers:
            step = run.step(number)
            if step is None:
                continue
            if step.status == "blocked":
                triggers.append(
                    CriticTrigger(
                        "step_blocked",
                        f"step {number} was recorded blocked: {step.note or 'no reason given'}",
                    )
                )
            elif step.status == "skipped":
                triggers.append(
                    CriticTrigger(
                        "step_skipped",
                        f"step {number} was skipped: {step.note or 'no reason given'}",
                    )
                )

    triggers.extend(
        _evidence_triggers(
            _scan(step_messages), failure_trigger=failure_trigger, scope=scope
        )
    )

    constraint = _constraint_trigger(approval_constraints)
    if constraint is not None:
        triggers.append(constraint)

    if always and not any(t.code == "step_resolved" for t in triggers):
        triggers.insert(
            0,
            CriticTrigger(
                "step_resolved",
                f"{scope} reached a terminal status; every step of an approved plan is checked",
            ),
        )
    return triggers


def describe(triggers: Sequence[CriticTrigger]) -> str:
    """The gate's reasons, as one block for the critic's prompt."""
    return "\n".join(f"- {trigger.code}: {trigger.detail}" for trigger in triggers)

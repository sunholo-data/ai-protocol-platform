"""AILANG inbox listing → A2UI board (two-way workbench, phase 1).

Registers a result→A2UI mapping for ``ailang_inbox_list`` on the proven 7.3
path (:mod:`adk.a2ui_result_render`), so a listing becomes an **Inbox** tab on
the workbench: unread counts per inbox, then one card per message — grouped by
inbox — with a CheckBox the user ticks.

Design: the two-way workbench design (§4.2, §4.3, §5).

Selection lives in the DATA MODEL, not the component tree. Each CheckBox binds
``/selected/<key>`` (a boolean per message), so the per-turn snapshot the
browser sends with every chat message (``forwardedProps.a2ui_surface_state``)
carries exactly what the user ticked, and the next turn's instructions show it
(``adk.a2ui_surface_context``). Nothing else has to be wired for the AI to see
"the ticked ones".

Refs, never ids (CLAUDE.md #9): the tool result already carries only refs
("#3"); the board shows the same numbers the AI speaks, and the data model
holds no coordinator id.

Message text is DATA from outside the conversation. Full bodies use explicit
body Text in a reader; the generic catalog renders safe Markdown, never actions.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from adk.a2ui_direct_actions import ActionResult, DirectActionContext, register_action
from adk.a2ui_ppa_render import _Tree
from adk.a2ui_result_render import BASIC_CATALOG_ID, register

INBOX_TOOL = "ailang_inbox_list"
INBOX_SURFACE_ID = "inbox_board"
INBOX_KIND = "inbox_board"

# Full text stays in reader components, outside the model snapshot.
_OPEN_MAX = 12000

# Buttons that hand the ticked messages to the AI, as a chat turn.
_ASK_AI = (
    ("Summarise these", "Summarise the messages I've ticked on the Inbox board."),
    ("Draft replies", "Draft replies to the messages I've ticked on the Inbox board."),
    ("Send to…", "Help me pass the messages I've ticked on the Inbox board on to another inbox."),
)

# The data model is re-sent with every chat turn and re-injected into the
# prompt, so keep each card's excerpt short; the AI reads the full message with
# ailang_inbox_read when it needs it.
_DM_EXCERPT = 160


def _age(created_at: Any, now: datetime | None = None) -> str:
    """'3h ago' / '2d ago' from an ISO timestamp; '' when it can't be read."""
    if not created_at:
        return ""
    try:
        ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    secs = max(0, int(((now or datetime.now(UTC)) - ts).total_seconds()))
    if secs < 3600:
        return f"{max(1, secs // 60)}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def selection_key(ref: str) -> str:
    """Data-model key for a ref: "#3" → "m3" (a JSON-pointer-safe object key)."""
    return "m" + str(ref).lstrip("#")


def _text_at(tree: _Tree, cid: str, text: str) -> str:
    """A Text with a STABLE id, so a direct action can patch it later
    (``updateComponents`` upserts by id)."""
    tree.text(text)
    tree.components[-1]["id"] = cid
    return cid


def _preview(text: str) -> str:
    """One bounded, readable line; full Markdown belongs in the reader."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, (dict, list)):
        fields = payload if isinstance(payload, dict) else {}
        text = next(
            (
                fields[k]
                for k in ("summary", "description", "message", "content", "body", "error_msg", "reason")
                if isinstance(fields.get(k), str) and fields[k].strip()
            ),
            "Structured message — open to view fields.",
        )
    elif re.match(r'^\s*\{\s*"', text):
        # Listing excerpts may end in the middle of serialized JSON.
        text = "Structured message — open to view fields."
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_#>|]", "", text)
    text = " ".join(text.split())
    return text[:140] + ("…" if len(text) > 140 else "")


def _status_text(status: str, age: str) -> str:
    mark = "✓ read" if status == "read" else ""
    return " · ".join(p for p in (mark, f"Mailbox: {status or 'unknown'}", age) if p)


def _messages(result: Any) -> list[dict[str, Any]] | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    msgs = [m for m in result.get("messages") or [] if isinstance(m, dict)]
    # Refs come from a session (the tool always has one in a chat turn). A
    # listing without refs has nothing the user can tick or name — no board.
    if not all(m.get("ref") for m in msgs):
        return None
    return msgs


def _prior_selection(tool_context: Any) -> dict[str, bool]:
    """Ticks on the board as the user left it — this turn's surface snapshot.

    A re-listing replaces the board; ticks on messages still shown survive.
    """
    state = getattr(tool_context, "state", None)
    if state is None:
        return {}
    try:
        snap = (state.get("a2ui_surface_state") or {}).get(INBOX_SURFACE_ID) or {}
        sel = ((snap.get("dataModel") or {}).get("selected")) or {}
    except AttributeError:
        return {}
    return {str(k): True for k, v in sel.items() if v is True} if isinstance(sel, dict) else {}


def inbox_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Build the Inbox board for an ``ailang_inbox_list`` result."""
    msgs = _messages(typed_result)
    if msgs is None:
        return None
    result: dict[str, Any] = typed_result
    now = datetime.now(UTC)
    tree = _Tree()

    counts = result.get("counts_by_inbox") or {}
    by_inbox: dict[str, list[dict[str, Any]]] = {}
    for m in msgs:
        by_inbox.setdefault(str(m.get("inbox") or "?"), []).append(m)
    # Order groups by the overview's counts when there is one, else by size.
    order = sorted(by_inbox, key=lambda box: (-int(counts.get(box, len(by_inbox[box]))), box))

    if counts:
        total = sum(int(v) for v in counts.values())
        state_label = "unread" if (result.get("filter") or {}).get("unread_only", True) else "messages"
        heading = f"{total} {state_label} across {len(counts)} inboxes"
        summary = " · ".join(f"{box} {n}" for box, n in counts.items())
    else:
        heading = f"{order[0]} — {len(msgs)} shown" if len(order) == 1 else f"{len(msgs)} messages"
        summary = ""
    children = [
        tree.text("AILANG messages", variant="h2"),
        tree.text(
            "Mailbox read/unread is separate from work progress. Open a message for its report and related chat."
        ),
        tree.text(heading, variant="h3", prefix="ib-h"),
    ]
    filters = result.get("filter") or {}
    if filters:
        scope = filters.get("inbox") or "all accessible inboxes"
        children.append(
            tree.text(f"Showing {'unread messages' if filters.get('unread_only') else 'all message states'} · {scope}")
        )
    if result.get("counts_note"):
        children.append(tree.text(str(result["counts_note"])))
    if summary:
        children.append(tree.text(summary, variant="body", prefix="ib-sum"))
    children.append(
        tree.text(
            "Select messages for bulk actions. Open shows the full message without marking it read.", prefix="ib-hint"
        )
    )
    children.append(
        tree.row(
            [
                tree.text_field(path="/query", label="Search loaded messages", prefix="ib-search"),
                tree.choice_picker(
                    path="/inboxFilter", options=[("all", "All inboxes"), *[(box, box) for box in order]], label="Inbox"
                ),
                tree.choice_picker(
                    path="/statusFilter",
                    options=[("all", "All states"), ("unread", "Unread"), ("read", "Read")],
                    label="Mailbox state",
                ),
                tree.button(
                    "Apply filters",
                    {
                        "event": {
                            "name": "direct:filter",
                            "context": {
                                "query": {"path": "/query"},
                                "inbox": {"path": "/inboxFilter"},
                                "status": {"path": "/statusFilter"},
                            },
                        }
                    },
                ),
            ],
            prefix="ib-filters",
        )
    )
    for comp in tree.components:
        if comp["component"] == "ChoicePicker":
            comp["displayStyle"] = "chips"
    children.append(_text_at(tree, "ib-visible", f"{len(msgs)} loaded messages"))
    # Direct actions (no model turn): the click carries the live selection,
    # resolved from the data model by the renderer; the server re-checks it
    # against what this board showed.
    selection = {"selected": {"path": "/selected"}}
    children.append(
        tree.row(
            [
                tree.button("Select visible", {"event": {"name": "direct:select_visible"}}),
                tree.button("Clear selection", {"event": {"name": "direct:clear_selection"}}),
                tree.button(
                    "Mark read",
                    {"event": {"name": "direct:mark_read", "context": selection}},
                    variant="primary",
                    prefix="ib-read",
                ),
                tree.button(
                    "Mark unread", {"event": {"name": "direct:mark_unread", "context": selection}}, prefix="ib-unread"
                ),
            ],
            prefix="ib-tools",
        )
    )
    # AI-mediated actions (mode B): the click posts a chat turn, and that turn
    # carries the board snapshot — so "these" resolves to the ticks (the
    # `Selected:` line). chat:send, not surface-action-run: the answer belongs
    # in the chat thread, which a surface-action-run never writes to.
    children.append(
        tree.row(
            [
                tree.button(
                    label,
                    {
                        "event": {
                            "name": "chat:send",
                            "context": {"prompt": prompt, "requireSelection": True, "selected": {"path": "/selected"}},
                        }
                    },
                    prefix="ib-ask",
                )
                for label, prompt in _ASK_AI
            ],
            prefix="ib-asks",
        )
    )

    prior = _prior_selection(tool_context)
    selected: dict[str, bool] = {}
    items: list[dict[str, Any]] = []
    for group_index, box in enumerate(order):
        group = by_inbox[box]
        children.append(_text_at(tree, f"ib-g-{group_index}", f"Messages to {box} ({len(group)})"))
        cards = []
        for m in group:
            ref = str(m["ref"])
            key = selection_key(ref)
            selected[key] = prior.get(key, False)
            title = str(m.get("title") or "").strip() or "(no title)"
            sender = str(m.get("from") or "")
            excerpt = str(m.get("excerpt") or "")
            age = _age(m.get("created_at"), now)
            status = str(m.get("status") or "")
            items.append(
                {
                    "key": key,
                    "ref": ref,
                    "title": title,
                    "inbox": box,
                    "from": sender,
                    "age": age,
                    "status": status,
                    "group": group_index,
                    "row": f"ib-card-{key}",
                    "excerpt": excerpt[:_DM_EXCERPT] + ("…" if len(excerpt) > _DM_EXCERPT else ""),
                }
            )
            body_id = _text_at(tree, f"ib-body-{key}", "Loading message…")
            tree.components[-1]["variant"] = "body"
            reader_details = [
                tree.text(f"{ref}  {title}", variant="h2"),
                tree.text(f"From {sender} · To {box}"),
                tree.text(f"Message type: {m.get('category') or 'unspecified'} · Work status: not reported"),
                body_id,
            ]
            related = m.get("related_thread")
            if isinstance(related, dict):
                sid, skill = str(related.get("session_id") or ""), str(related.get("skill_id") or "")
                if all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in (sid, skill)):
                    path = f"/chat/{quote(skill, safe='')}"
                    reader_details.append(
                        tree.row(
                            [
                                tree.button(
                                    "Open related thread",
                                    {
                                        "functionCall": {
                                            "call": "openUrl",
                                            "args": {"url": f"{path}?session={quote(sid, safe='')}"},
                                        }
                                    },
                                ),
                                tree.button(
                                    "Open skill in a new chat",
                                    {"functionCall": {"call": "openUrl", "args": {"url": path}}},
                                ),
                            ]
                        )
                    )
            reader = tree.column(reader_details)
            trigger = tree.button("Open", {"event": {"name": "direct:open", "context": {"ref": ref}}}, prefix="ib-open")
            modal_id = f"ib-modal-{key}"
            tree.components.append({"id": modal_id, "component": "Modal", "trigger": trigger, "content": reader})
            top = tree.row(
                [
                    tree.checkbox(path=f"/selected/{key}", label=f"{ref}  {title}", prefix="ib-cb"),
                    _text_at(tree, f"ib-st-{key}", _status_text(status, age)),
                    modal_id,
                ],
                justify="spaceBetween",
                prefix="ib-top",
            )
            meta = f"{sender} · {_preview(excerpt)}" if excerpt else sender
            details = [top, _text_at(tree, f"ib-meta-{key}", meta)]
            tree.card(tree.column(details, prefix="ib-col"), prefix="ib-card")
            tree.components[-1]["id"] = f"ib-card-{key}"
            cards.append(f"ib-card-{key}")
        tree.column(cards, prefix="ib-list")
        tree.components[-1]["id"] = f"ib-list-{group_index}"
        children.append(f"ib-list-{group_index}")
    tree.root(children)

    messages = tree.messages()
    messages[0]["createSurface"]["catalogId"] = BASIC_CATALOG_ID
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": INBOX_SURFACE_ID,
                "value": {
                    "kind": INBOX_KIND,
                    "heading": heading,
                    "items": items,
                    "selected": selected,
                    "visible": [i["key"] for i in items],
                    "query": "",
                    "inboxFilter": ["all"],
                    "statusFilter": ["all"],
                },
            },
        }
    )
    return messages


def _inbox_artifact(typed_result: Any) -> dict[str, Any] | None:
    msgs = _messages(typed_result)
    if msgs is None:
        return None
    counts = typed_result.get("counts_by_inbox") or {}
    if counts:
        description = f"{sum(int(v) for v in counts.values())} unread across {len(counts)} inboxes"
    else:
        boxes = sorted({str(m.get("inbox") or "?") for m in msgs})
        description = f"{len(msgs)} messages in {', '.join(boxes)}" if boxes else "No messages in this listing"
    return {"kind": INBOX_KIND, "title": "Inbox", "description": description}


# --- direct actions (two-way workbench mode C) ------------------------------


def _patch_text(cid: str, text: str, *, body: bool = False) -> dict[str, Any]:
    comp = {"id": cid, "component": "Text", "text": text}
    if body:
        comp["variant"] = "body"
    return {"version": "v0.9", "updateComponents": {"surfaceId": INBOX_SURFACE_ID, "components": [comp]}}


def _patch_data(path: str, value: Any) -> dict[str, Any]:
    return {"version": "v0.9", "updateDataModel": {"surfaceId": INBOX_SURFACE_ID, "path": path, "value": value}}


def _tool_ctx(ctx: DirectActionContext) -> SimpleNamespace:
    # The inbox tools resolve refs from session state (ailang_inbox_refs).
    return SimpleNamespace(state=dict(ctx.state))


def _label(item: dict[str, Any]) -> str:
    return f"{item['ref']} “{item.get('title') or ''}”"


async def _mark(ctx: DirectActionContext, unread: bool) -> ActionResult:
    from tools.ailang_inbox import ailang_inbox_ack

    shown = ctx.shown()
    by_key = {selection_key(i.get("ref", "")): (n, i) for n, i in enumerate(shown) if i.get("ref")}
    sel = ctx.action_context.get("selected")
    keys = [str(k) for k, v in sel.items() if v is True] if isinstance(sel, dict) else []
    if not keys:
        return ActionResult("error", "Tick one or more messages first.")
    # Only refs this board showed — never one the client made up.
    if any(k not in by_key for k in keys):
        return ActionResult("error", "Some ticked messages aren't on this board — list the inbox again.")
    refs = [by_key[k][1]["ref"] for k in keys]
    out = await ailang_inbox_ack(", ".join(refs), mark_unread=unread, tool_context=_tool_ctx(ctx))
    if out.get("status") == "error" and "results" not in out:
        return ActionResult("error", str(out.get("error") or "Couldn't mark them."))
    results = out.get("results") or {}
    verb = "unread" if unread else "read"
    done, failed, patches = [], [], []
    for key, ref in zip(keys, refs, strict=True):
        n, item = by_key[key]
        outcome = str(results.get(ref, "failed: no result"))
        if outcome.startswith("failed"):
            failed.append(f"{ref} ({outcome.removeprefix('failed: ')})")
            continue
        done.append(item)
        patches += [
            _patch_text(f"ib-st-{key}", _status_text(verb, str(item.get("age") or ""))),
            _patch_data(f"/items/{n}/status", verb),
            _patch_data(f"/selected/{key}", False),
        ]
    message = f"Marked {len(done)} {verb}" + (f" ({', '.join(i['ref'] for i in done)})" if done else "")
    if failed:
        message += f"; failed: {'; '.join(failed)}"
    status = "ok" if not failed else ("partial" if done else "error")
    record = f"The user marked {', '.join(_label(i) for i in done)} {verb} on the Inbox board." if done else ""
    return ActionResult(status, message + ".", patches, record)


async def _mark_read(ctx: DirectActionContext) -> ActionResult:
    return await _mark(ctx, unread=False)


async def _mark_unread(ctx: DirectActionContext) -> ActionResult:
    return await _mark(ctx, unread=True)


async def _open(ctx: DirectActionContext) -> ActionResult:
    from tools.ailang_inbox import ailang_inbox_read

    ref = str(ctx.action_context.get("ref") or "")
    item = next((i for i in ctx.shown() if i.get("ref") == ref), None)
    if item is None:
        return ActionResult("error", "That message isn't on this board — list the inbox again.")
    out = await ailang_inbox_read(ref, tool_context=_tool_ctx(ctx))
    if out.get("status") != "ok":
        error = str(out.get("error") or "Couldn't open it.")
        return ActionResult("error", error, [_patch_text(f"ib-body-{selection_key(ref)}", error, body=True)])
    msg = out["message"]
    text = str(msg.get("content") or "")
    body = text[:_OPEN_MAX] + ("\n[cut — ask in chat for the rest]" if len(text) > _OPEN_MAX else "")
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, (dict, list)):
        # A coordinator may store JSON as a string. Pretty-print it as code,
        # without turning any fields into actions or trusting embedded markup.
        pretty = json.dumps(payload, indent=2, ensure_ascii=False)
        fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", pretty)), default=0))
        full = f"{fence}json\n{pretty}\n{fence}"
    else:
        full = body
    return ActionResult(
        "ok",
        f"Opened {ref}.",
        [_patch_text(f"ib-body-{selection_key(ref)}", full, body=True)],
        f"The user opened {_label(item)} on the Inbox board.",
    )


async def _select_visible(ctx: DirectActionContext) -> ActionResult:
    model = ctx.data_model()
    keys = {i["key"] for i in ctx.shown()}
    visible = [key for key in model.get("visible", []) if key in keys]
    return ActionResult(
        "ok", f"Selected {len(visible)} visible messages.", [_patch_data(f"/selected/{key}", True) for key in visible]
    )


async def _clear_selection(ctx: DirectActionContext) -> ActionResult:
    return ActionResult("ok", "Selection cleared.", [_patch_data("/selected", {i["key"]: False for i in ctx.shown()})])


async def _filter(ctx: DirectActionContext) -> ActionResult:
    """Filter the trusted loaded list without fetching or acknowledging messages."""

    def choice(value: Any) -> str:
        return str(value[0] if isinstance(value, list) and value else value or "all")

    query = str(ctx.action_context.get("query") or "")[:200].strip().casefold()
    box = choice(ctx.action_context.get("inbox"))
    status = choice(ctx.action_context.get("status"))
    items = ctx.shown()
    if (box != "all" and box not in {i["inbox"] for i in items}) or status not in {"all", "read", "unread"}:
        return ActionResult("error", "Choose an inbox and mailbox state from this board.")
    visible = [
        i
        for i in items
        if (box == "all" or i["inbox"] == box)
        and (status == "all" or i["status"] == status)
        and (not query or query in " ".join(str(i.get(k) or "") for k in ("title", "from", "excerpt")).casefold())
    ]
    components = []
    for group in sorted({i["group"] for i in items}):
        group_items = [i for i in visible if i["group"] == group]
        name = next(i["inbox"] for i in items if i["group"] == group)
        components.extend(
            [
                {"id": f"ib-list-{group}", "component": "Column", "children": [i["row"] for i in group_items]},
                {"id": f"ib-g-{group}", "component": "Text", "text": f"Messages to {name} ({len(group_items)})"},
            ]
        )
    label = (
        f"{len(visible)} of {len(items)} loaded messages"
        if visible
        else "No loaded messages match. Clear the search or choose All to see them again."
    )
    patches = [
        {"version": "v0.9", "updateComponents": {"surfaceId": INBOX_SURFACE_ID, "components": components}},
        _patch_data("/visible", [i["key"] for i in visible]),
        _patch_data("/query", str(ctx.action_context.get("query") or "")[:200]),
        _patch_data("/inboxFilter", [box]),
        _patch_data("/statusFilter", [status]),
        _patch_text("ib-visible", label),
    ]
    return ActionResult("ok", label, patches)


register_action(surface_kind=INBOX_KIND, name="filter", handler=_filter, tell_ai=False)
register_action(surface_kind=INBOX_KIND, name="select_visible", handler=_select_visible, tell_ai=False)
register_action(surface_kind=INBOX_KIND, name="clear_selection", handler=_clear_selection, tell_ai=False)

register_action(surface_kind=INBOX_KIND, name="mark_read", handler=_mark_read, requires_tool="ailang_inbox_ack")
register_action(surface_kind=INBOX_KIND, name="mark_unread", handler=_mark_unread, requires_tool="ailang_inbox_ack")
register_action(surface_kind=INBOX_KIND, name="open", handler=_open, requires_tool="ailang_inbox_read")


register(
    inbox_to_a2ui,
    tool_names=[INBOX_TOOL],
    name="inbox-board",
    surface=INBOX_SURFACE_ID,
    artifact_meta=_inbox_artifact,
)

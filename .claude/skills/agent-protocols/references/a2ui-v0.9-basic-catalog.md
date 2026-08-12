# A2UI v0.9 "Basic" Catalog — component vocabulary

The full set of components the `@a2ui/react` renderer supports out of the box
(v0.9 "Basic" catalog, formerly "Standard"). **Reach for this before designing
any A2UI surface** — don't re-derive it from the schema each time.

Source of truth (enumerate live if this drifts):
```python
from a2ui.basic_catalog import BasicCatalog
BasicCatalog.get_config("0.9")
# or read the JSON: .venv/.../site-packages/a2ui/assets/0.9/basic_catalog.json
```
The SDK also injects the schema into the model prompt automatically via
`render_as_llm_instructions` — skills describe UI *conceptually*, they do NOT
re-spec the wire format (see memory `feedback_a2ui_skill_prompts`).

## Components (name → key props)

**Layout / structure**
| Component | Key props | Use for |
|-----------|-----------|---------|
| `Column` | `children`, `justify`, `align` | vertical stack |
| `Row` | `children`, `justify`, `align` | horizontal stack |
| `List` | `children`, `direction`, `align` | repeated items (diff rows, clause rows) |
| `Card` | `child` | a bordered section container |
| `Divider` | `axis` | horizontal/vertical rule |
| `Tabs` | `tabs` | **section navigation** (e.g. Differences · Contract A · Contract B) |
| `Modal` | `trigger`, `content` | overlay dialog |

**Content**
| Component | Key props | Use for |
|-----------|-----------|---------|
| `Text` | `text`, `variant` | headings/body (variant = h1/h2/body/caption…) |
| `Icon` | `name` | inline icon |
| `Image` | `url`, `description`, `fit`, `variant` | images |
| `AudioPlayer` | `url`, `description` | audio |
| `Video` | `url` | video |

**Interactive / input**
| Component | Key props | Use for |
|-----------|-----------|---------|
| `Button` | `child`, `variant`, `action` | actions → surface-action |
| `ChoicePicker` | `label`, `options`, `value`, `variant`, `displayStyle`, `filterable` | select / multi-select (**severity + clause filters**) |
| `CheckBox` | `label`, `value` | boolean toggle |
| `TextField` | `label`, `value`, `variant`, `validationRegexp` | text input |
| `Slider` | `label`, `min`, `max`, `value` | numeric (e.g. clause-depth) |
| `DateTimeInput` | `value`, `enableDate`, `enableTime`, `min`, `max`, `label` | date/time |

## Notes

- **No `Table` / `Accordion`** in Basic. Build tables as `Column` of `Row`s (or
  a `List`); build collapsible sections with `Tabs` or a data-model "selected
  section" key toggling `Column` visibility.
- **Refs, not inline children:** `children`/`child`/`content`/`trigger` are
  component-**id strings**, not nested objects. The tree root MUST have
  `id: "root"`.
- **Dynamic values:** `{"path": "/key"}` binds to the surface data model;
  literals are bare strings/numbers. Update via an `updateDataModel` message.
- **Interaction:** `Button.action` / input `value` changes fire client actions
  → `surface-action` (fire-and-forget) or `surface-action-run` (write + agent
  run). See `action-triggered-agent-turn.md`.
- **Custom catalogs:** ship an Aitana-branded component set via
  `CatalogConfig.from_path()` without forking the renderer (v6 uses Basic only
  so far).

Verified against `a2ui==0.9.x` in backend/.venv on 2026-07-09.

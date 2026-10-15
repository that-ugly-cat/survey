# Item banks — specification

*9 October 2026. Status: implemented (`banks.py`, wired in `main.py`, `flow.py`, `mcp_app.py`; tested in `test_banks.py`). Where the implementation departs from the draft, the text below says so.*

## 1. The problem

Pools draw **pages**. A pool with `show_count = k` enumerates every combination of `k` of its pages
(`itertools.combinations` in `_assign_condition`) and hands out the least-used one. That is right for
vignette studies with a handful of pages, and it cannot express a design where each participant gets
a sample of **items** from a bank, each item exists in several **versions**, and the share of each
version depends on the participant's **arm**.

The case that prompted this: a provenance-discrimination study (the AI coding Turing test). 80
passages, each in two versions (human coding, model coding). Every participant judges 10 passages.
Three arms differ in how many of the 10 are model-coded: 3, 5 or 7. Across the sample, every passage
must appear about equally often, and about equally often in each version, so that a passage's content
cannot masquerade as an effect of provenance.

Done with pools, this is 160 pages drawn 10 at a time: about 10¹⁶ combinations to enumerate on the
first page load, and no way to say "3 of these must be version L". The workaround, pre-built
booklets assigned by a `show_count = 1` pool, works but fixes the sample of item combinations in
advance and needs a placeholder page per booklet.

The same shape recurs beyond this study: vignette banks with several framings per vignette, stimulus
sets with counterbalanced versions, any design where items are sampled and their versions are
manipulated.

## 2. The concept

An **item bank** is a randomization object that lives beside pools in a survey.

- **Item**: a unit of content, identified by a key (`P001`).
- **Version**: one rendering of an item, identified by a label (`H`, `L`). **Each version is one page
  of the schema**, so every version keeps its own question names, and an answer always lands in a
  column that says which item and which version it belongs to.
- **Draw**: per participant, `k` items, a version for each, and an order.
- **Arm**: a named quota of versions for the draw (`{"H": 7, "L": 3}`). The arm is drawn first and
  exposed as a variable, like a pool's condition.

Pages stay the unit the client already knows how to show, hide and reorder. A bank only decides
**which pages, in which order** — the same two things a pool with a `page_order` decides today.

### Why pages and not a template filled from a data file

A single template page per slot, filled from a CSV of items, would keep the schema small. It was
considered and set aside: answers would land in columns named by slot (`slot3_quality`) instead of by
item, every downstream tool (results, aggregates, review export, MCP summaries) would read the wrong
unit, and the client would need a templating layer SurveyJS does not have as data binding. Generating
a large schema is a one-off cost paid by a script; misnamed columns are paid at every analysis.

## 3. Configuration

```json
{
  "name": "passages",
  "items": [
    {"item": "P001", "versions": {"H": "P001_H", "L": "P001_L"}},
    {"item": "P002", "versions": {"H": "P002_H", "L": "P002_L"}}
  ],
  "show_count": 10,
  "arm_var": "arm",
  "arms": [
    {"name": "LLM30", "quota": {"H": 7, "L": 3}},
    {"name": "LLM50", "quota": {"H": 5, "L": 5}},
    {"name": "LLM70", "quota": {"H": 3, "L": 7}}
  ],
  "order": "random"
}
```

| field | meaning |
|---|---|
| `items` | each item's versions, as `{label: page name}`. Items may have different version sets |
| `show_count` | `k`, items drawn per participant |
| `arms` | optional. Each arm's `quota` must sum to `k` and use only version labels the bank has. Without arms the bank balances versions freely (see 4.3) |
| `arm_var` | the SurveyJS variable that carries the drawn arm. Required when `arms` is set |
| `order` | `random`: a fresh permutation per participant. `fixed`: schema order of the drawn pages |

A **naming convention helper** builds `items` for the common case: given a regex with named groups
(default `^(?P<item>.+)_(?P<version>[^_]+)$`) the admin page proposes the item list from the schema's
page names, to be confirmed before saving.

## 4. Allocation

Run once per participant on `GET /s/{slug}`, inside one `BEGIN IMMEDIATE` transaction so two
simultaneous arrivals cannot read the same counts. **Load** means completed draws plus draws issued in
the last `PENDING_ASSIGNMENT_MINUTES` and not completed, exactly as pools count today: an abandoned
start stops distorting the balance once it ages out.

### 4.1 Arm

The least-loaded arm, ties broken at random. Arms stay within one participant of each other among
completed responses, give or take what is in flight.

### 4.2 Items

The `k` items with the lowest exposure load. Ties at the boundary are broken at random, so the draw
does not walk the bank in schema order. Items that cannot satisfy the arm's quota are skipped (an item
without version `L` cannot fill an `L` slot); see 4.3.

### 4.3 Versions

Given the drawn items and the arm's quota, assign versions so that the quota is met exactly and
**each item moves toward balance across its versions**. This is a small minimum-cost assignment: `k`
items to `k` slots, where the slots are the quota expanded (`H` ×7, `L` ×3), and the cost of putting
item *i* in a slot of version *v* is the current load of *(i, v)* plus a random jitter in (0, 1) to
break ties. With `k ≤ 50` the Hungarian algorithm costs nothing; a pure-Python implementation of a few
dozen lines avoids a new dependency.

Without arms, the slots are filled to minimize the same cost with no quota: each item gets its
least-loaded version.

### 4.4 Order

`random`: a uniform permutation of the drawn pages. `fixed`: their schema order.

### 4.5 What the client receives

The client code does not change. The server adds the bank's pages to `pool_pages` (all of them) and
the drawn ones to `assigned_pages`, so the existing loop hides every undrawn version; it appends
`{"var": arm_var, "pages": [drawn pages in order]}` to `page_orders`, so the existing reorder puts the
drawn pages into the slots they occupy, in draw order; and it sets `conditions[arm_var] = arm`. A bank
therefore needs nothing new in `survey.html` beyond carrying its assignment id back on submit, which
the existing `_assignment_ids` path already does if bank ids are kept distinct (prefix `b`, see 5).

### 4.6 Guarantees, stated as tests

After N simulated completed participants with no abandonment:

- every draw meets its arm's quota exactly (a hard constraint);
- arm counts differ by at most 1 (guaranteed by 4.1);
- item exposure differs by at most 1 across items, when every item can serve every arm (guaranteed by
  4.2);
- item×version balance is a **target, not a guarantee**: the quota fixes how many slots of each version
  a draw has, so the items drawn together compete for them. The test asserts that over 300 simulated
  participants no two-version item is off by more than 2, and reports the distribution; if the bound
  fails, 4.3 moves from a per-draw assignment to choosing items and versions jointly.

With abandonment, the same bounds hold over completed draws plus the pending window, which is what the
algorithm sees.

## 5. Storage

```sql
CREATE TABLE item_banks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id   INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    bank_name   TEXT NOT NULL,
    bank_order  INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL            -- the object in section 3
);

CREATE TABLE bank_draws (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id    INTEGER NOT NULL REFERENCES item_banks(id) ON DELETE CASCADE,
    arm        TEXT NULL,
    draw_json  TEXT NOT NULL,            -- [{"item", "version", "page"}] in display order
    issued_at  TEXT NOT NULL DEFAULT (datetime('now')),
    completed  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_bank_draws ON bank_draws (bank_id, completed, issued_at);
```

Loads for arms, items and item×version are computed from `bank_draws` at allocation time. At the
scale of a survey (hundreds of draws, a hundred items) a scan is cheap; no counters to keep in step.

The response keeps its own record of what it was shown, so the data survive a deleted bank:

```json
"_banks": {"passages": {"arm": "LLM30", "draw_id": 41,
                        "items": ["P017", ...], "versions": ["L", ...], "pages": ["P017_L", ...]}}
```

*As implemented:* parallel lists rather than a list of objects, because the flat export turns a
list into one `;`-joined column and a list of objects into an unreadable one. The record is written
by the server at submit, from `bank_draws`; a `_banks` sent by the client is discarded.

`_assignment_ids` carries `b41` for the draw; `_complete_assignments` marks `bank_draws` completed for
`b`-prefixed ids, scoped to the survey's banks as pool ids are scoped today.

## 6. Interactions and rules

- A page may belong to **at most one** bank, and a bank page may not also be in a pool. Validation
  refuses the overlap.
- Banks and pools coexist: pools for between-subject conditions that are not item sampling, banks for
  items. Their variables are independent.
- Common pages (consent, demographics, debrief) stay outside both and are shown to everyone.
- Bank pages may still carry a `visibleIf`; it is evaluated as usual after the draw. Validation warns
  when a bank page's `visibleIf` could hide a drawn page, because a hidden drawn page silently shortens
  the participant's sample.
- **Saving a bank's configuration resets its draws**, as saving a pool does, and the admin page says so
  before the save when the survey already holds responses.

## 7. Admin page

A section **Item banks** on the Randomization page, under the pools:

- **Create**: name, `k`, order, arm variable; items from the naming helper (preview table: item ×
  versions, with unmatched pages listed) or pasted JSON; arms as rows of version quotas, with the sum
  checked against `k` as it is typed. *As implemented:* items and arms are JSON fields; the sum of
  each quota is checked in the browser as it is typed, and the server refuses at save with the
  reasons and the typed values kept.
- **Status**: per arm, completed and in-flight draws. Per item, exposure. Per item×version, counts, with
  the largest imbalance highlighted. A line that answers "is it balanced?" in one sentence.
- **Reset** and **Delete**, with the same confirmations pools have.
- A `?` modal on the pattern of the existing ones: what a bank is, how it differs from a pool, what
  "load" means, why saving resets.

## 8. MCP surface

- `set_bank(slug, config, bank_id=None)`: create or replace; returns validation findings, like
  `set_pool`.
- `delete_bank(slug, bank_id)`.
- `bank_status(slug)`: the status of section 7, in numbers, no response content.
- `preview_flow`: banks multiply the arms by their arm values. Since draws cannot be enumerated, each
  arm shows **one example draw** marked as an example, and states `k` and the quota.
- `validate_survey`: the checks of section 9.

## 9. Validation

Errors, refused at save:

- a version page that is not in the schema, or that sits in another bank or in a pool;
- `show_count` larger than the number of items able to serve an arm's quota;
- an arm quota that does not sum to `k`, or names a version label no item has;
- a quota for version *v* larger than the number of items that have *v*;
- `arm_var` missing when arms are set, or colliding with a question name or a pool variable.

Warnings:

- items with fewer versions than the arms use (they are drawn less often);
- bank pages with a `visibleIf` (section 6);
- question names on version pages that do not follow `<page>_<role>`, which the long export needs to
  line up answers across versions (section 10);
- an expected exposure below 5 per item×version at the target sample size, when the admin enters one.

The pool fix travels with this work: **a pool whose `C(n, k)` exceeds 10,000 is refused at save**, with
the count in the message. Today such a pool saves fine and blocks the server at the first page load.

## 10. Export

- **Flat export (CSV, XLSX, JSON)**: unchanged columns, plus `_banks.<name>.arm`,
  `_banks.<name>.items` (items in display order) and `_banks.<name>.versions`.
- **Long export**, new, one row per response × drawn item: `response_id`, `submitted_at`, `bank`, `arm`,
  `position`, `item`, `version`, `page`, then one column per **role**, where a role is the question name
  with the page-name prefix removed (`P017_L_quality` → `quality`). This is the table a mixed model
  wants, and it is the reason for the naming rule in section 9.

## 11. Limits

Items ≤ 1,000; versions per item ≤ 8; `k` ≤ 50; arms ≤ 12. Above them the save is refused with the
reason.

## 12. Tests

- **Allocation**, simulated: the guarantees of 4.6 for 300 participants on the Turing-test
  configuration, and on a bank with unequal version sets; the quota always met; random tie-breaking
  (two runs with different seeds give different draws).
- **Concurrency**: two allocations in parallel threads do not both take the last least-loaded item.
- **Abandonment**: pending draws older than the window stop counting.
- **Round trip**: `GET` hides undrawn versions and orders drawn pages, submit marks the draw completed,
  the response carries `_banks`, the long export lines answers up by role.
- **Validation**: each error and warning of section 9.
- **Pool guard**: a pool over the combination cap is refused.

## 13. Out of scope

- Adaptive draws that depend on earlier answers, beyond choosing the arm (section 15).
- Weighted arms (unequal allocation ratios). Pools do not have them either; when needed, they belong to
  both.
- Within-item version comparison by the same participant. A bank never shows two versions of one item
  to one person, by construction.

## 14. The Turing-test configuration, as a check

80 items `P001`–`P080`, versions `H`/`L`, `k = 10`, arms `LLM30` (7 H, 3 L), `LLM50` (5/5), `LLM70`
(3 H, 7 L), `arm_var = arm`, `order = random`. At 150 completed participants: 50 per arm, each passage
shown 18–19 times, about 9 times in each version (to within the bound of 4.6). Over a full cycle of the
three arms the version slots are even (7+5+3 = 15 of each), so the global H/L split is exact whenever the
arms are. The schema has 160 version pages plus the common
ones; each version page carries the passage, the code list with definitions, `P0xx_V_quality` (1–7)
and `P0xx_V_prov` (six points).

## 15. The arm from the answers (`arm_from`)

*Added 15 October 2026 for a longitudinal design: three parallel forms rotated across three waves, so
that each participant sees a different form at each wave without anyone keeping a list of who got
which.*

### The problem

Arms are drawn at the page load, balanced across arrivals. A rotation needs the arm to follow the
person: form 1 at the first wave means form 2 at the second. The only thing that follows a person in a
pseudonymous study is something they answer, typically a self-generated code, and the page load comes
before any answer.

### The design

`arm_from` names a question, or a calculated value of the schema, whose value is an arm name. With it:

1. **At the page load** the bank draws one sequence **per arm** (`allocate_deferred`): for each arm,
   the items and versions its quota calls for, in an order, as an ordinary draw would. They are stored
   together in the ledger (`draw_json = {"by_arm": {arm: draw}}`) with the arm left empty. All of the
   bank's pages are pooled and none is assigned, so nothing shows yet.
2. **In the browser**, as soon as the answer named by `arm_from` is known, the pages of that arm
   show, in the drawn order, in the slots the bank's pages occupy; the other arms' pages stay hidden.
   Changing the answer reapplies, so a participant who goes back and corrects it walks the right
   sequence.
3. **At the submit** the server reads the arm from the stored answers (the question, or the
   calculated value saved with the result) and resolves the draw to that arm: the ledger keeps that
   arm's sequence only, and the response records it as for any other draw. The browser never states
   what it showed; and only an arm the server drew for this person can be chosen. An answer that names
   no arm resolves nothing, and the response records no bank rather than a guess.

A rotation then lives in the schema. With a day of birth `day` and one survey per wave:

```json
"calculatedValues": [{
  "name": "form", "includeIntoResult": true,
  "expression": "iif(({day} + 2) % 3 = 0, 'F1', iif(({day} + 2) % 3 = 1, 'F2', 'F3'))"
}]
```

where the constant (here 2) is the wave number. A pool cannot take its condition this way; a form that
is a single page is a bank with one item and one version per arm.

### What it gives up

- **Balance.** The arms are as large as the answers make them. A day of birth splits three ways almost
  evenly (days 29–31 are rarer); other sources may not. Save warns about it.
- **Items within an arm** are still balanced, from the resolved draws only: an unresolved draw does
  not count, because which arm it showed is unknown.

### Validation

`arm_from` requires arms; it cannot equal `arm_var`; it must be a question or a calculated value of the
schema; a calculated value must be saved with the answers (`includeIntoResult`), or the server could
not read it; a question must be asked before the first page of the bank.

### Tests

`test_arm_from.py`: validation, one draw per arm, the page load, the resolution at submit, an answer
naming no arm, loads and status counting resolved draws only, the admin form keeping the field, and an
ordinary bank unchanged. The browser side (showing, reordering, reapplying after a correction) was
checked by hand on a local server.

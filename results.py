"""Turning a report and its aggregates into something a page can draw.

`aggregate.py` counts and says who may see what; this module puts labels on the
numbers, renders the text blocks, and emits a chart payload that is deliberately
not a Chart.js configuration. The library is vendored at one version, and a
payload written in its dialect would make swapping it a rewrite of this file
rather than of the fifty lines of JavaScript that read the payload.

Pure module: no FastAPI, no database.
"""

import html

import markdown as md_lib

import aggregate
import report as report_model
from review_export import LANGS

# A chart payload is {chart, labels, series, stacked, percent, mine}. `series`
# is a list of {label, data}; one entry is a plain bar or column, several are
# stacked. `mine` marks the viewer's own answer, and is None for everyone else.

# The page's own words. The questionnaire and the report's text blocks carry
# their translations in the schema; these lines are generated here, and without
# them an Italian results page says "12 people wrote something" in English,
# which is the half-translated page the whole locale machinery exists to avoid.
# Missing keys fall back to English one at a time rather than the whole locale.
STRINGS = {
    "en": {
        "answered": "answered", "skipped": "skipped", "asked": "were asked",
        "numbers": "the numbers", "responses": "responses", "response": "response",
        "updated": "updated", "own_marked": "your own answers are marked",
        "nothing": "Nothing has been published in this report yet.",
        "footer": "The figures above are counts of the answers collected so far. "
                  "Cells with fewer than five answers are hidden.",
        "not_enough": "Not enough answers yet to show a breakdown ({n} so far).",
        "all_hidden": "Every group here holds fewer than five people, so the breakdown "
                      "stays hidden. The numbers below say how many answered in all.",
        "no_chart": "No chart for a “{type}” question.",
        "orphan": "“{name}” is not in the questionnaire any more.",
        "wrote": "{n} people wrote something",
        "median_length": ", median length {k} characters",
        "selections": "{s} selections from {r} people: percentages are of people, "
                      "so they add up to more than 100.",
        "mean": "mean", "median": "median", "sd": "SD", "range": "range",
        "hidden": "hidden (fewer than five)", "hidden_cell": "<5",
        "th_answer": "Answer", "th_n": "n", "th_pct": "%", "th_statement": "Statement",
        "th_date": "Date", "th_value": "Value", "th_rows": "Rows added",
        "th_people": "people", "th_mean_rank": "mean rank", "th_option": "Option",
        "aud_public": "visible to anyone with the link",
        "aud_respondent": "shown to respondents after they submit",
        "aud_owner": "private to you",
        "as_label": "looking as", "as_me": "me", "as_resp": "a respondent",
        "as_public": "anyone",
        "previewing": "This is what {who} sees. Anything held back for them is held back here too.",
        "other": "Other",
        "everyone": "everyone", "by": "by",
    },
    "it": {
        "answered": "hanno risposto", "skipped": "hanno saltato",
        "asked": "hanno visto la domanda", "numbers": "i numeri",
        "responses": "risposte", "response": "risposta", "updated": "aggiornato",
        "own_marked": "le tue risposte sono evidenziate",
        "nothing": "In questo report non è ancora stato pubblicato niente.",
        "footer": "Le cifre qui sopra contano le risposte raccolte finora. "
                  "Le celle con meno di cinque risposte sono nascoste.",
        "not_enough": "Ancora troppe poche risposte per mostrare una distribuzione "
                      "({n} finora).",
        "all_hidden": "Ogni gruppo qui ha meno di cinque persone, quindi la distribuzione "
                      "resta nascosta. I numeri qui sotto dicono quanti hanno risposto in tutto.",
        "no_chart": "Nessun grafico per una domanda di tipo “{type}”.",
        "orphan": "“{name}” non è più nel questionario.",
        "wrote": "{n} persone hanno scritto qualcosa",
        "median_length": ", lunghezza mediana {k} caratteri",
        "selections": "{s} selezioni da {r} persone: le percentuali sono sulle persone, "
                      "quindi sommano a più di 100.",
        "mean": "media", "median": "mediana", "sd": "DS", "range": "intervallo",
        "hidden": "nascosto (meno di cinque)", "hidden_cell": "<5",
        "th_answer": "Risposta", "th_n": "n", "th_pct": "%",
        "th_statement": "Affermazione", "th_date": "Data", "th_value": "Valore",
        "th_rows": "Righe aggiunte", "th_people": "persone",
        "th_mean_rank": "rango medio", "th_option": "Opzione",
        "aud_public": "visibile a chiunque abbia il link",
        "aud_respondent": "mostrato a chi risponde dopo l'invio",
        "aud_owner": "privato, solo tuo",
        "as_label": "guardo come", "as_me": "me", "as_resp": "chi ha risposto",
        "as_public": "chiunque",
        "previewing": "Questo è quello che vede {who}. Quello che a loro è nascosto è nascosto anche qui.",
        "other": "Altro",
        "everyone": "tutti", "by": "per",
    },
    "de": {
        "answered": "haben geantwortet", "skipped": "haben übersprungen",
        "asked": "wurden gefragt", "numbers": "die Zahlen",
        "responses": "Antworten", "response": "Antwort", "updated": "aktualisiert",
        "own_marked": "Ihre eigenen Antworten sind markiert",
        "nothing": "In diesem Bericht wurde noch nichts veröffentlicht.",
        "footer": "Die Zahlen oben zählen die bisher gesammelten Antworten. "
                  "Zellen mit weniger als fünf Antworten sind ausgeblendet.",
        "not_enough": "Noch zu wenige Antworten für eine Verteilung ({n} bisher).",
        "all_hidden": "Jede Gruppe hier umfasst weniger als fünf Personen, daher bleibt die "
                      "Verteilung ausgeblendet.",
        "no_chart": "Kein Diagramm für eine Frage vom Typ „{type}“.",
        "orphan": "„{name}“ ist nicht mehr im Fragebogen.",
        "wrote": "{n} Personen haben etwas geschrieben",
        "median_length": ", Medianlänge {k} Zeichen",
        "selections": "{s} Auswahlen von {r} Personen: die Prozentwerte beziehen sich "
                      "auf Personen und ergeben daher mehr als 100.",
        "mean": "Mittelwert", "median": "Median", "sd": "SD", "range": "Spannweite",
        "hidden": "ausgeblendet (weniger als fünf)", "hidden_cell": "<5",
        "th_answer": "Antwort", "th_n": "n", "th_pct": "%", "th_statement": "Aussage",
        "th_date": "Datum", "th_value": "Wert", "th_rows": "Zeilen hinzugefügt",
        "th_people": "Personen", "th_mean_rank": "mittlerer Rang", "th_option": "Option",
        "aud_public": "für alle mit dem Link sichtbar",
        "aud_respondent": "Teilnehmenden nach dem Absenden gezeigt",
        "aud_owner": "nur für Sie",
        "as_label": "Ansicht als", "as_me": "ich", "as_resp": "Teilnehmende",
        "as_public": "alle",
        "previewing": "So sieht es {who}. Was dort verborgen bleibt, bleibt auch hier verborgen.",
        "other": "Andere",
        "everyone": "alle", "by": "nach",
    },
    "fr": {
        "answered": "ont répondu", "skipped": "ont passé",
        "asked": "ont vu la question", "numbers": "les chiffres",
        "responses": "réponses", "response": "réponse", "updated": "mis à jour",
        "own_marked": "vos propres réponses sont indiquées",
        "nothing": "Rien n'a encore été publié dans ce rapport.",
        "footer": "Les chiffres ci-dessus comptent les réponses recueillies jusqu'ici. "
                  "Les cellules de moins de cinq réponses sont masquées.",
        "not_enough": "Trop peu de réponses pour une distribution ({n} jusqu'ici).",
        "all_hidden": "Chaque groupe ici compte moins de cinq personnes, la distribution "
                      "reste donc masquée.",
        "no_chart": "Pas de graphique pour une question de type « {type} ».",
        "orphan": "« {name} » n'est plus dans le questionnaire.",
        "wrote": "{n} personnes ont écrit quelque chose",
        "median_length": ", longueur médiane {k} caractères",
        "selections": "{s} sélections de {r} personnes : les pourcentages portent sur "
                      "les personnes, ils dépassent donc 100.",
        "mean": "moyenne", "median": "médiane", "sd": "ET", "range": "étendue",
        "hidden": "masqué (moins de cinq)", "hidden_cell": "<5",
        "th_answer": "Réponse", "th_n": "n", "th_pct": "%", "th_statement": "Affirmation",
        "th_date": "Date", "th_value": "Valeur", "th_rows": "Lignes ajoutées",
        "th_people": "personnes", "th_mean_rank": "rang moyen", "th_option": "Option",
        "aud_public": "visible par quiconque a le lien",
        "aud_respondent": "montré aux répondants après envoi",
        "aud_owner": "privé",
        "as_label": "vu comme", "as_me": "moi", "as_resp": "un répondant",
        "as_public": "tout le monde",
        "previewing": "Voici ce que voit {who}. Ce qui leur est masqué l'est ici aussi.",
        "other": "Autre",
        "everyone": "tout le monde", "by": "par",
    },
}


def strings(locale: str) -> dict:
    return {**STRINGS["en"], **STRINGS.get(locale, {})}


def loc(value, locale: str) -> str:
    """A localized string in the viewer's language.

    The order matters and is not obvious: the schema uses `default` for English
    because SurveyJS does, while a text block typed in the editor has no
    `default` to inherit and stores `en`. Both have to resolve.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return "" if value is None else str(value)
    for key in (locale, "default", "en"):
        text = value.get(key)
        if isinstance(text, str):
            return text
    for text in value.values():
        if isinstance(text, str):
            return text
    return ""


def render_markdown(block_md: dict, locale: str) -> str:
    """Markdown to HTML, with the source's own HTML escaped first.

    These blocks are written by the survey's owner, which is not a reason to
    leave a script tag's worth of daylight on a page served to strangers.
    Escaping before rendering costs nothing: markdown syntax survives it, and a
    literal tag comes out as the text it is.
    """
    text = loc(block_md, locale)
    if not text.strip():
        return ""
    return md_lib.markdown(html.escape(text), extensions=["tables", "sane_lists"])


# --- labels ---

def _choice_labels(el: dict, locale: str) -> dict:
    out = {}
    for c in el.get("choices") or []:
        if isinstance(c, dict):
            out[c.get("value")] = loc(c.get("text", c.get("value")), locale)
        else:
            out[c] = str(c)
    if el.get("showOtherItem"):
        # The author already wrote a word for this bucket, in the language of
        # the questionnaire. Falling back to a hardcoded "Other" puts English
        # on the axis of an Italian chart.
        out["other"] = loc(el.get("otherText"), locale) or strings(locale)["other"]
    return out


def _rate_labels(el: dict, locale: str) -> dict:
    out = {}
    for v in el.get("rateValues") or []:
        if isinstance(v, dict):
            out[v.get("value")] = loc(v.get("text", v.get("value")), locale)
        else:
            out[v] = str(v)
    return out


def _row_labels(el: dict, locale: str) -> dict:
    out = {}
    for r in el.get("rows") or []:
        if isinstance(r, dict):
            out[r.get("value")] = loc(r.get("text", r.get("value")), locale)
        else:
            out[r] = str(r)
    return out


def _column_labels(el: dict, locale: str) -> dict:
    out = {}
    for c in el.get("columns") or []:
        if isinstance(c, dict):
            key = c.get("value") if "value" in c else c.get("name")
            out[key] = loc(c.get("text", c.get("title", key)), locale)
        else:
            out[c] = str(c)
    return out


def _label(value, table: dict) -> str:
    if value in table:
        return table[value]
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value == "other":
        return "Other"
    return "" if value is None else str(value)


# --- chart payloads ---

def _cells_payload(cells, labels, chart, percent, total, mine=None):
    shown = [c for c in cells if not c.get("hide")]
    data = []
    for c in shown:
        if c.get("suppressed"):
            data.append(None)
        elif percent and total:
            data.append(round(100 * c["n"] / total, 1))
        else:
            data.append(c["n"])
    mine_index = None
    if mine is not None:
        wanted = mine if isinstance(mine, list) else [mine]
        mine_index = [i for i, c in enumerate(shown) if c["value"] in wanted] or None
    return {
        "chart": chart,
        "labels": [_label(c["value"], labels) for c in shown],
        "series": [{"label": None, "data": data}],
        "stacked": False,
        "percent": bool(percent),
        "suppressed": [i for i, c in enumerate(shown) if c.get("suppressed")],
        "mine": mine_index,
    }


def _stacked_payload(row_labels, series_labels, matrix, chart="stacked", mine=None):
    """`matrix` is one list of counts per series, each aligned to row_labels."""
    return {
        "chart": chart,
        "labels": row_labels,
        "series": [{"label": series_labels[i], "data": matrix[i]}
                   for i in range(len(series_labels))],
        "stacked": True,
        "percent": True,
        "suppressed": [],
        "mine": mine,
    }


def _table(columns, rows):
    return {"columns": columns, "rows": rows}


def _cells_table(cells, labels, total, t):
    rows = []
    for c in cells:
        if c.get("suppressed"):
            rows.append([_label(c["value"], labels), t["hidden_cell"], "—"])
        else:
            pct = f"{100 * c['n'] / total:.1f}%" if total else "—"
            rows.append([_label(c["value"], labels), str(c["n"]), pct])
    return _table([t["th_answer"], t["th_n"], t["th_pct"]], rows)


# --- per shape ---

def _view_categorical(agg, el, block, locale, mine):
    labels = _choice_labels(el, locale)
    cells = list(agg.get("cells") or [])
    if block.get("other") == "hide":
        cells = [c for c in cells if c["value"] != "other"]
    if block.get("sort") == "frequency":
        cells.sort(key=lambda c: (c["n"] is None, -(c["n"] or 0)))
    total = agg.get("n") or 0
    chart = block.get("chart") or "bar"
    percent = block.get("value") == "percent"
    return ([_cells_payload(cells, labels, chart, percent, total, mine)],
            [_cells_table(cells, labels, total, strings(locale))], None)


def _view_multi(agg, el, block, locale, mine):
    payloads, tables, _ = _view_categorical(agg, el, block, locale, mine)
    note = strings(locale)["selections"].format(s=agg.get("selections", 0),
                                                r=agg.get("respondents", 0))
    return payloads, tables, note


def _view_binary(agg, el, block, locale, mine):
    labels = {True: loc(el.get("labelTrue", "Yes"), locale),
              False: loc(el.get("labelFalse", "No"), locale)}
    total = agg.get("n") or 0
    chart = block.get("chart") or "segmented"
    return ([_cells_payload(agg.get("cells") or [], labels, chart,
                            block.get("value") == "percent", total, mine)],
            [_cells_table(agg.get("cells") or [], labels, total, strings(locale))], None)


def _view_scale(agg, el, block, locale, mine):
    labels = _rate_labels(el, locale)
    total = agg.get("n") or 0
    chart = block.get("chart") or "column"
    t = strings(locale)
    s = agg.get("summary") or {}
    note = None
    if s.get("n"):
        note = f"{t['mean']} {s['mean']}, {t['median']} {s['median']}"
        if s.get("sd") is not None:
            note += f", {t['sd']} {s['sd']}"
    return ([_cells_payload(agg.get("cells") or [], labels, chart,
                            block.get("value") == "percent", total, mine)],
            [_cells_table(agg.get("cells") or [], labels, total, t)], note)


def _view_grid(agg, el, block, locale, mine):
    rows = agg.get("rows") or []
    col_labels = _column_labels(el, locale)
    row_labels = _row_labels(el, locale)
    columns = agg.get("columns") or []
    series_labels = [_label(c, col_labels) for c in columns]
    matrix = []
    for ci, col in enumerate(columns):
        line = []
        for row in rows:
            cell = next((c for c in row.get("cells") or [] if c["value"] == col), None)
            n = None if not cell or cell.get("suppressed") else cell["n"]
            total = row.get("n") or 0
            line.append(round(100 * n / total, 1) if n is not None and total else None)
        matrix.append(line)
    labels = [_label(r["value"], row_labels) + f"  (n={r.get('n', 0)})" for r in rows]
    table_rows = []
    for row in rows:
        cells = {c["value"]: c for c in row.get("cells") or []}
        table_rows.append([_label(row["value"], row_labels), str(row.get("n", 0))] +
                          [strings(locale)["hidden_cell"]
                           if cells.get(c, {}).get("suppressed")
                           else str(cells.get(c, {}).get("n", 0)) for c in columns])
    t = strings(locale)
    return ([_stacked_payload(labels, series_labels, matrix)],
            [_table([t["th_statement"], t["th_n"]] + series_labels, table_rows)], None)


def _view_grid_of_questions(agg, el, block, locale, mine):
    row_labels = _row_labels(el, locale)
    payloads, tables = [], []
    for col in agg.get("columns") or []:
        title = _label(col["column"], _column_labels(el, locale))
        rows = col.get("rows") or []
        if col.get("open"):
            t = strings(locale)
            filled = sum(r.get("n", 0) for r in rows)
            texts = col.get("texts")
            tables.append(_table(
                [title, t["th_answer"]],
                [[title, x] for x in texts] if texts
                else [[title, t["wrote"].format(n=filled) + "."]]))
            continue
        if col.get("cell_type") == "rating":
            data = [(r.get("summary") or {}).get("mean") for r in rows]
            payloads.append({
                "chart": "bar", "title": title,
                "labels": [_label(r["value"], row_labels) for r in rows],
                "series": [{"label": "mean", "data": data}],
                "stacked": False, "percent": False, "suppressed": [], "mine": None,
            })
            tables.append(_table([title, strings(locale)["th_n"], strings(locale)["mean"]],
                                 [[_label(r["value"], row_labels), str(r.get("n", 0)),
                                   str((r.get("summary") or {}).get("mean", "—"))]
                                  for r in rows]))
            continue
        values = []
        for r in rows:
            for c in r.get("cells") or []:
                if c["value"] not in values:
                    values.append(c["value"])
        choices = _choice_labels(el, locale)
        matrix = []
        for v in values:
            line = []
            for r in rows:
                cell = next((c for c in r.get("cells") or [] if c["value"] == v), None)
                n = None if not cell or cell.get("suppressed") else cell["n"]
                total = r.get("n") or 0
                line.append(round(100 * n / total, 1) if n is not None and total else None)
            matrix.append(line)
        p = _stacked_payload([_label(r["value"], row_labels) for r in rows],
                             [_label(v, choices) for v in values], matrix)
        p["title"] = title
        payloads.append(p)
        t = strings(locale)
        tables.append(_table([title, t["th_n"]] + [_label(v, choices) for v in values],
                             [[_label(r["value"], row_labels), str(r.get("n", 0))] +
                              [t["hidden_cell"]
                               if (next((c for c in r.get("cells") or []
                                         if c["value"] == v), {}) or {}).get("suppressed")
                               else str(next((c["n"] for c in r.get("cells") or []
                                              if c["value"] == v), 0))
                               for v in values]
                              for r in rows]))
    return payloads, tables, None


def _view_repeating(agg, el, block, locale, mine):
    payloads, tables = [], []
    col_titles = _column_labels(el, locale)
    for col in agg.get("columns") or []:
        title = _label(col["column"], col_titles)
        if col.get("open"):
            t = strings(locale)
            texts = col.get("texts")
            tables.append(_table(
                [title, t["th_answer"]],
                [[title, x] for x in texts] if texts
                else [[title, t["wrote"].format(n=col.get("n", 0)) + "."]]))
            continue
        if col.get("cells"):
            labels = _choice_labels(el, locale)
            p = _cells_payload(col["cells"], labels, "bar", False, col.get("n") or 0)
            p["title"] = title
            payloads.append(p)
            tables.append(_cells_table(col["cells"], labels, col.get("n") or 0,
                                       strings(locale)))
    sizes = agg.get("rows_per_respondent") or []
    if sizes:
        t = strings(locale)
        tables.append(_table([t["th_rows"], t["th_people"]],
                             [[str(c["value"]), str(c["n"])] for c in sizes]))
    return payloads, tables, None


def _view_ranking(agg, el, block, locale, mine):
    labels = _choice_labels(el, locale)
    items = agg.get("items") or []
    depth = agg.get("depth") or 0
    matrix = [[it["positions"][p] if p < len(it["positions"]) else 0 for it in items]
              for p in range(depth)]
    p = _stacked_payload([_label(i["value"], labels) for i in items],
                         [f"{n + 1}" for n in range(depth)], matrix)
    p["percent"] = False
    t = strings(locale)
    return ([p],
            [_table([t["th_option"], t["th_n"], t["th_mean_rank"]] +
                    [f"#{n + 1}" for n in range(depth)],
                    [[_label(i["value"], labels), str(i.get("n", 0)),
                      str(i.get("mean_rank", "—"))] +
                     [str(x) for x in i["positions"]] for i in items])], None)


def _view_numeric(agg, el, block, locale, mine):
    bins = agg.get("bins") or []
    labels = [f"{b['from']:g}" if b["from"] == b["to"] else f"{b['from']:g}–{b['to']:g}"
              for b in bins]
    t = strings(locale)
    s = agg.get("summary") or {}
    note = None
    if s.get("n"):
        note = f"{t['median']} {s['median']}, {t['range']} {s['min']}–{s['max']}"
        if s.get("mean") is not None:
            note = f"{t['mean']} {s['mean']}, " + note
    return ([{"chart": "histogram", "labels": labels,
              "series": [{"label": None, "data": [b["n"] for b in bins]}],
              "stacked": False, "percent": False, "suppressed": [], "mine": None}],
            [_table([t["th_value"], t["th_n"]],
                     [[l, str(b["n"])] for l, b in zip(labels, bins)])],
            note)


def _view_temporal(agg, el, block, locale, mine):
    cells = agg.get("cells") or []
    return ([{"chart": "line", "labels": [str(c["value"]) for c in cells],
              "series": [{"label": None, "data": [c["n"] for c in cells]}],
              "stacked": False, "percent": False, "suppressed": [], "mine": None}],
            [_table([strings(locale)["th_date"], strings(locale)["th_n"]],
                     [[str(c["value"]), str(c["n"])] for c in cells])], None)


def _view_open(agg, el, block, locale, mine):
    """Open answers. The texts are only ever present for the owner, because
    `for_audience` removed them long before this module saw the aggregate."""
    t = strings(locale)
    texts = agg.get("texts")
    note = t["wrote"].format(n=agg.get("n", 0))
    if agg.get("median_length"):
        note += t["median_length"].format(k=int(agg["median_length"]))
    if texts is None:
        return [], [], note + "."
    return [], [_table([t["th_answer"]], [[x] for x in texts])], note + "."


_VIEWS = {
    "categorical": _view_categorical, "multi": _view_multi, "binary": _view_binary,
    "scale": _view_scale, "grid": _view_grid,
    "grid_of_questions": _view_grid_of_questions, "repeating": _view_repeating,
    "ranking": _view_ranking, "numeric": _view_numeric, "temporal": _view_temporal,
    "open": _view_open, "open_counts": _view_open,
}


# --- assembly ---

def build(schema: dict, pools, report: dict, responses: list, viewer: str,
          locale: str = "en", mine: dict = None, by: str = None) -> dict:
    """The blocks a viewer sees, each one ready to draw.

    `mine` is the respondent's own answers, which mark their place in the
    charts and are not otherwise used.
    """
    words = strings(locale)
    blocks = report_model.resolve(report, schema, viewer)
    names = [b["name"] for b in blocks if b["kind"] == "question"]
    by = by if viewer == aggregate.OWNER else None
    summary = aggregate.summarise(schema, pools, responses, only=names, by=by)
    aggs = {q["name"]: q for q in summary["questions"]}
    elements = {el.get("name"): el for _, el, _ in aggregate.elements_of(schema)}

    out = []
    for block in blocks:
        if block["kind"] == "text":
            body = render_markdown(block.get("md"), locale)
            if body:
                out.append({"kind": "text", "html": body})
            continue

        name = block["name"]
        agg = aggs.get(name)
        if agg is None or agg.get("orphan"):
            if viewer == aggregate.OWNER:
                out.append({"kind": "orphan", "name": name,
                            "note": words["orphan"].format(name=name)})
            continue

        el = elements.get(name, {})
        # Filtered for whoever is reading, not for how far the block may travel.
        # The block's audience decides *whether* this person sees it at all, and
        # `resolve` has already applied that; masking the owner's own numbers
        # because the block is also published would hide the data from the one
        # person entitled to it.
        shown = aggregate.for_audience(agg, viewer)
        entry = {
            "kind": "question", "name": name,
            "title": loc(el.get("title"), locale) or name,
            "description": loc(el.get("description"), locale),
            "n": shown.get("n", 0), "missing": shown.get("missing", 0),
            "exposed": shown.get("exposed", 0),
            "charts": [], "tables": [], "note": None, "below_threshold": False,
        }
        if shown.get("below_threshold"):
            entry["below_threshold"] = True
            entry["note"] = words["not_enough"].format(n=shown.get("n", 0))
            out.append(entry)
            continue

        view = _VIEWS.get(shown.get("shape"))
        if view is None:
            entry["note"] = words["no_chart"].format(type=el.get("type"))
            out.append(entry)
            continue
        own = (mine or {}).get(name)
        charts, tables, note = view(shown, el, block, locale, own)
        # A chart with nothing above zero left to draw is an empty canvas, which
        # reads as a bug rather than as a rule. Masked cells are None and unchosen
        # options are a genuine zero: neither draws anything, so both count as
        # nothing here. The table stays, with the masks visible as masks.
        drawable = [p for p in charts
                    if any(isinstance(v, (int, float)) and v > 0
                           for s in p.get("series", []) for v in s.get("data", []))]
        if charts and not drawable:
            note = words["all_hidden"]
        entry["charts"], entry["tables"], entry["note"] = drawable, tables, note
        if by and agg.get("groups"):
            entry["groups"] = []
            for value, group in agg["groups"].items():
                g = aggregate.for_audience(group, viewer)
                if g.get("below_threshold") or not _VIEWS.get(g.get("shape")):
                    continue
                gcharts, gtables, _ = _VIEWS[g["shape"]](g, el, block, locale, None)
                entry["groups"].append({"value": value, "n": g.get("n", 0),
                                        "charts": gcharts, "tables": gtables})
        out.append(entry)

    return {"blocks": out, "responses": summary["responses"]}

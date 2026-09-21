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
        "explore": "Explore", "explore_title": "Do these two move together?",
        "explore_lede": "Pick two questions. The test is chosen from the shape of "
                        "the answers, not by you, and what comes back is a direction "
                        "to look in rather than a finding.",
        "explore_x": "First variable", "explore_y": "Second variable",
        "explore_run": "Compare", "explore_none": "Nothing to compare yet.",
        "lbl_test": "test", "lbl_effect": "effect", "lbl_answered_both": "answered both",
        "t_spearman": "Spearman rank correlation", "t_mannwhitney": "Mann-Whitney U",
        "t_kruskal": "Kruskal-Wallis H", "t_chi2": "chi-square of independence",
        "t_fisher": "Fisher exact test",
        "e_rho": "rho", "e_rank_biserial": "rank-biserial",
        "e_cramers_v": "Cramér's V", "e_epsilon_squared": "epsilon squared",
        "b_negligible": "negligible", "b_small": "small",
        "b_moderate": "moderate", "b_large": "large",
        "c_exploratory": "Exploratory. This is a direction to look in, not a result "
                         "to report.",
        "c_multiple": "This questionnaire offers {pairs} pairs. Trying them all, about "
                      "one in twenty comes out under 0.05 with nothing behind it.",
        "c_small_n": "Only {n} people answered both, which is too few for the p-value "
                     "to mean much.",
        "c_small_group": "At least one group holds fewer than five people.",
        "c_expected_small": "{cells} of {of} cells expect fewer than five, where the "
                            "chi-square approximation is unreliable.",
        "err_too_few": "Fewer than {minimum} people answered both questions.",
        "err_no_variation": "One of the two never varies, so there is nothing to compare.",
        "err_same_variable": "That is the same variable twice.",
        "err_unknown_variable": "No such variable in this questionnaire.",
        "th_median": "median", "th_mean": "mean",
        "c_cells_hidden": "Cells holding fewer than five people are hidden, as they "
                          "are everywhere else on this page. The test above them is "
                          "computed on all of it.",
        "c_groups_hidden": "Groups of fewer than five people are left out of the table.",
        "err_not_published": "That question is not published on this page.",
        "explore_public": "Let readers cross two questions themselves",
        "explore_public_hint": "For citizen science: anyone reading the page can pick "
                               "two published questions and see whether they move "
                               "together. They get the test and the effect size; cells "
                               "under five people stay hidden, and twenty people must "
                               "have answered both before anything is shown.",
        "explore_how": "How to read this", "explore_back": "back",
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
        "explore": "Esplora", "explore_title": "Queste due si muovono insieme?",
        "explore_lede": "Scegli due domande. Il test lo sceglie la forma delle risposte, "
                        "non tu, e quello che torna è una direzione in cui guardare, "
                        "non un risultato.",
        "explore_x": "Prima variabile", "explore_y": "Seconda variabile",
        "explore_run": "Confronta", "explore_none": "Non c'è ancora niente da confrontare.",
        "lbl_test": "test", "lbl_effect": "effetto",
        "lbl_answered_both": "hanno risposto a entrambe",
        "t_spearman": "correlazione di Spearman sui ranghi", "t_mannwhitney": "U di Mann-Whitney",
        "t_kruskal": "H di Kruskal-Wallis", "t_chi2": "chi quadro di indipendenza",
        "t_fisher": "test esatto di Fisher",
        "e_rho": "rho", "e_rank_biserial": "rank-biserial",
        "e_cramers_v": "V di Cramér", "e_epsilon_squared": "epsilon quadro",
        "b_negligible": "trascurabile", "b_small": "piccolo",
        "b_moderate": "medio", "b_large": "grande",
        "c_exploratory": "Esplorativo. È una direzione in cui guardare, non un risultato "
                         "da riportare.",
        "c_multiple": "Questo questionario offre {pairs} coppie. Provandole tutte, circa "
                      "una su venti scende sotto 0,05 senza niente dietro.",
        "c_small_n": "Hanno risposto a entrambe solo in {n}: troppo pochi perché il p "
                     "voglia dire molto.",
        "c_small_group": "Almeno un gruppo ha meno di cinque persone.",
        "c_expected_small": "{cells} celle su {of} attendono meno di cinque, e lì "
                            "l'approssimazione del chi quadro non è affidabile.",
        "err_too_few": "Hanno risposto a entrambe le domande meno di {minimum} persone.",
        "err_no_variation": "Una delle due non varia mai, quindi non c'è niente da confrontare.",
        "err_same_variable": "È due volte la stessa variabile.",
        "err_unknown_variable": "Questa variabile non è in questo questionario.",
        "th_median": "mediana", "th_mean": "media",
        "c_cells_hidden": "Le celle con meno di cinque persone sono nascoste, come "
                          "ovunque su questa pagina. Il test qui sopra è calcolato "
                          "su tutto.",
        "c_groups_hidden": "I gruppi con meno di cinque persone restano fuori dalla tabella.",
        "err_not_published": "Quella domanda non è pubblicata su questa pagina.",
        "explore_public": "Lascia incrociare due domande a chi legge",
        "explore_public_hint": "Per la citizen science: chi legge la pagina può scegliere "
                               "due domande pubblicate e vedere se si muovono insieme. "
                               "Ottiene il test e l'ampiezza dell'effetto; le celle sotto "
                               "le cinque persone restano nascoste, e servono venti "
                               "persone che abbiano risposto a entrambe.",
        "explore_how": "Come si legge", "explore_back": "indietro",
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
        "explore": "Erkunden", "explore_title": "Bewegen sich diese beiden zusammen?",
        "explore_lede": "Zwei Fragen auswählen. Den Test bestimmt die Form der Antworten, "
                        "nicht Sie, und heraus kommt eine Richtung zum Nachschauen, "
                        "kein Befund.",
        "explore_x": "Erste Variable", "explore_y": "Zweite Variable",
        "explore_run": "Vergleichen", "explore_none": "Noch nichts zu vergleichen.",
        "lbl_test": "Test", "lbl_effect": "Effekt", "lbl_answered_both": "beantworteten beide",
        "t_spearman": "Spearman-Rangkorrelation", "t_mannwhitney": "Mann-Whitney-U",
        "t_kruskal": "Kruskal-Wallis-H", "t_chi2": "Chi-Quadrat-Unabhängigkeitstest",
        "t_fisher": "exakter Test nach Fisher",
        "e_rho": "rho", "e_rank_biserial": "rangbiserial",
        "e_cramers_v": "Cramérs V", "e_epsilon_squared": "Epsilon-Quadrat",
        "b_negligible": "vernachlässigbar", "b_small": "klein",
        "b_moderate": "mittel", "b_large": "groß",
        "c_exploratory": "Explorativ. Eine Richtung zum Nachschauen, kein Ergebnis zum "
                         "Berichten.",
        "c_multiple": "Dieser Fragebogen bietet {pairs} Paare. Probiert man alle, fällt "
                      "etwa eines von zwanzig ohne Grund unter 0,05.",
        "c_small_n": "Nur {n} Personen beantworteten beide Fragen, zu wenige für einen "
                     "aussagekräftigen p-Wert.",
        "c_small_group": "Mindestens eine Gruppe umfasst weniger als fünf Personen.",
        "c_expected_small": "{cells} von {of} Zellen erwarten weniger als fünf; dort ist "
                            "die Chi-Quadrat-Näherung unzuverlässig.",
        "err_too_few": "Weniger als {minimum} Personen beantworteten beide Fragen.",
        "err_no_variation": "Eine der beiden variiert nie, es gibt nichts zu vergleichen.",
        "err_same_variable": "Das ist zweimal dieselbe Variable.",
        "err_unknown_variable": "Diese Variable gibt es in diesem Fragebogen nicht.",
        "th_median": "Median", "th_mean": "Mittelwert",
        "c_cells_hidden": "Zellen mit weniger als fünf Personen bleiben ausgeblendet, "
                          "wie überall auf dieser Seite. Der Test darüber rechnet mit "
                          "allem.",
        "c_groups_hidden": "Gruppen unter fünf Personen bleiben aus der Tabelle heraus.",
        "err_not_published": "Diese Frage ist auf dieser Seite nicht veröffentlicht.",
        "explore_public": "Leser zwei Fragen selbst kreuzen lassen",
        "explore_public_hint": "Für Citizen Science: wer die Seite liest, kann zwei "
                               "veröffentlichte Fragen wählen und sehen, ob sie "
                               "zusammenhängen. Test und Effektstärke ja, Zellen unter "
                               "fünf Personen nein, und erst wenn zwanzig Personen beide "
                               "beantwortet haben.",
        "explore_how": "Wie man das liest", "explore_back": "zurück",
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
        "explore": "Explorer", "explore_title": "Ces deux-là bougent-elles ensemble ?",
        "explore_lede": "Choisissez deux questions. Le test est choisi par la forme des "
                        "réponses, pas par vous, et ce qui revient est une piste, "
                        "pas un résultat.",
        "explore_x": "Première variable", "explore_y": "Deuxième variable",
        "explore_run": "Comparer", "explore_none": "Rien à comparer pour l'instant.",
        "lbl_test": "test", "lbl_effect": "effet", "lbl_answered_both": "ont répondu aux deux",
        "t_spearman": "corrélation des rangs de Spearman", "t_mannwhitney": "U de Mann-Whitney",
        "t_kruskal": "H de Kruskal-Wallis", "t_chi2": "khi-deux d'indépendance",
        "t_fisher": "test exact de Fisher",
        "e_rho": "rho", "e_rank_biserial": "rang-bisérial",
        "e_cramers_v": "V de Cramér", "e_epsilon_squared": "epsilon carré",
        "b_negligible": "négligeable", "b_small": "petit",
        "b_moderate": "moyen", "b_large": "grand",
        "c_exploratory": "Exploratoire. Une piste à suivre, pas un résultat à publier.",
        "c_multiple": "Ce questionnaire offre {pairs} paires. En les essayant toutes, "
                      "environ une sur vingt passe sous 0,05 sans rien derrière.",
        "c_small_n": "Seulement {n} personnes ont répondu aux deux, trop peu pour que le "
                     "p veuille dire grand-chose.",
        "c_small_group": "Au moins un groupe compte moins de cinq personnes.",
        "c_expected_small": "{cells} cellules sur {of} en attendent moins de cinq, là où "
                            "l'approximation du khi-deux n'est pas fiable.",
        "err_too_few": "Moins de {minimum} personnes ont répondu aux deux questions.",
        "err_no_variation": "L'une des deux ne varie jamais, il n'y a rien à comparer.",
        "err_same_variable": "C'est deux fois la même variable.",
        "err_unknown_variable": "Cette variable n'existe pas dans ce questionnaire.",
        "th_median": "médiane", "th_mean": "moyenne",
        "c_cells_hidden": "Les cellules de moins de cinq personnes sont masquées, "
                          "comme partout ailleurs sur cette page. Le test au-dessus "
                          "porte sur la totalité.",
        "c_groups_hidden": "Les groupes de moins de cinq personnes restent hors du tableau.",
        "err_not_published": "Cette question n'est pas publiée sur cette page.",
        "explore_public": "Laisser les lecteurs croiser deux questions",
        "explore_public_hint": "Pour la science participative : qui lit la page peut "
                               "choisir deux questions publiées et voir si elles varient "
                               "ensemble. Le test et la taille d'effet oui, les cellules "
                               "de moins de cinq personnes non, et vingt personnes "
                               "doivent avoir répondu aux deux.",
        "explore_how": "Comment lire ceci", "explore_back": "retour",
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


# What the "?" buttons open. Kept apart from STRINGS because these are
# paragraphs rather than labels, and because they carry the one thing a panel
# like this owes the person using it: why this test and not the familiar one.
#
# Written in English and Italian. A locale with no entry falls back to English
# per topic, which for several hundred words of statistical prose is a better
# outcome than a translation nobody checked.
HELP = {
    "en": {
        "how": {
            "title": "How to read what comes back",
            "body": [
                "**The p-value** is how often a pattern at least this strong would turn "
                "up if the two questions had nothing to do with each other. It is not "
                "the probability that there is something there, and it says nothing "
                "about how big the something is.",
                "**The asterisks** are the usual convention — * under 0.05, ** under "
                "0.01, *** under 0.001 — and they are a shorthand for thresholds "
                "somebody chose in 1925, not a measure of importance. Trying many "
                "pairs produces them at a steady rate with nothing behind them.",
                "**The effect size** is the one to read first. It says how strongly the "
                "two move together, and it does not grow just because more people "
                "answered. A large effect on few people and a tiny effect on many can "
                "carry the same p-value, and they mean very different things.",
                "**Moving together is not causing.** If two answers line up, it may be "
                "that one drives the other, that the other drives the one, that "
                "something absent from the questionnaire drives both, or that the "
                "people who chose to answer are not the people you meant to ask. "
                "Nothing here can tell those apart, and a survey rarely can at all.",
            ],
        },
        "spearman": {
            "title": "Spearman rank correlation",
            "body": [
                "Both answers are turned into ranks — first, second, third — and the "
                "correlation is computed on those. It asks whether one rises as the "
                "other rises, in any steady way.",
                "**Why not Pearson.** Pearson assumes the numbers sit on a scale where "
                "the distance between them means something, and behaves best when the "
                "relation is a straight line and the spread is roughly normal. A 1–5 "
                "rating is ordered, but nobody can say that the step from 4 to 5 is the "
                "same size as the step from 1 to 2, and with a few dozen answers there "
                "is no way to check the rest of it either.",
                "**What it costs.** Working on ranks throws away the size of the gaps, "
                "so a relation can be perfect here and curved in the data. And rho "
                "answers about order only: it will not notice a pattern that goes up "
                "and then down again.",
            ],
        },
        "mannwhitney": {
            "title": "Mann-Whitney U",
            "body": [
                "Two groups, one set of answers. Every answer is ranked against every "
                "other, and U counts how often a value from one group sits above a "
                "value from the other.",
                "**Why not a t-test.** A t-test compares means and leans on each group "
                "being roughly normal. Ratings are bounded at both ends, lumpy, and "
                "usually piled up near one end; with twenty or thirty people you cannot "
                "test that assumption well enough to rely on it. Ranks need none of it.",
                "**What it costs.** The question it answers is not \"are the means "
                "different\" but \"does one group tend to sit above the other\". If the "
                "two groups have very different spreads, it can react to that instead "
                "of to the shift you had in mind. The medians beside the result are "
                "there for that reason.",
            ],
        },
        "kruskal": {
            "title": "Kruskal-Wallis H",
            "body": [
                "Mann-Whitney with more than two groups: the same ranking, spread over "
                "three or more, asking whether they all come from the same place.",
                "**Why not ANOVA.** One-way ANOVA assumes normal groups with similar "
                "variances and compares means; the same objections apply as for the "
                "t-test, and more sharply with small groups.",
                "**What it costs.** A small p says at least one group differs from the "
                "others — not which one. Finding out which means comparing pairs, and "
                "each comparison is another chance for something to look real by "
                "accident, so this panel does not offer them.",
            ],
        },
        "chi2": {
            "title": "Chi-square of independence",
            "body": [
                "The counts in the table are compared with the counts you would expect "
                "if the two questions were unrelated. The further the table sits from "
                "that, the larger the statistic.",
                "**What it assumes.** That the expected counts are not tiny. Below "
                "about five in a cell the approximation starts to drift, and with a "
                "small survey and a question with many options that happens easily — "
                "which is why the panel counts those cells and tells you.",
                "**What it costs.** It says the two are not independent and nothing "
                "about how, or how much. That is what Cramér's V beside it is for, and "
                "why the table is printed underneath: the shape of the association is "
                "in the cells, not in the p-value.",
            ],
        },
        "fisher": {
            "title": "Fisher exact test",
            "body": [
                "On a two-by-two table the exact probability of every table with the "
                "same row and column totals can simply be added up, and the p-value is "
                "the share of them at least as lopsided as the one observed.",
                "**Why it is used here.** No approximation, so nothing to be wrong "
                "about when the counts are small — exactly the case where chi-square "
                "becomes unreliable. On a 2×2 it costs nothing to compute, so the panel "
                "always prefers it.",
                "**What it costs.** It fixes the margins of the table as given, which "
                "is a stricter model of the sampling than most surveys actually follow, "
                "and it tends to be a little conservative as a result.",
            ],
        },
    },
    "it": {
        "how": {
            "title": "Come si legge quello che torna",
            "body": [
                "**Il p** dice quanto spesso comparirebbe una regolarità forte almeno "
                "così, se fra le due domande non ci fosse niente. Non è la probabilità "
                "che qualcosa ci sia, e non dice niente su quanto sia grande.",
                "**Gli asterischi** sono la convenzione di sempre — * sotto 0,05, ** "
                "sotto 0,01, *** sotto 0,001 — e sono l'abbreviazione di soglie che "
                "qualcuno ha scelto nel 1925, non una misura di importanza. Provando "
                "molte coppie compaiono a ritmo regolare senza niente dietro.",
                "**L'ampiezza dell'effetto** è quella da leggere per prima. Dice quanto "
                "forte le due cose si muovono insieme, e non cresce solo perché hanno "
                "risposto in più. Un effetto grande su poche persone e un effetto minimo "
                "su molte possono avere lo stesso p, e vogliono dire cose molto diverse.",
                "**Muoversi insieme non è causare.** Se due risposte si accordano, può "
                "darsi che una tiri l'altra, che sia il contrario, che qualcosa che nel "
                "questionario non c'è tiri entrambe, o che chi ha scelto di rispondere "
                "non sia chi volevi interrogare. Niente qui dentro sa distinguere questi "
                "casi, e una survey raramente ci riesce del tutto.",
            ],
        },
        "spearman": {
            "title": "Correlazione di Spearman sui ranghi",
            "body": [
                "Le due risposte vengono trasformate in ranghi — primo, secondo, terzo — "
                "e la correlazione si calcola su quelli. Chiede se una sale mentre "
                "l'altra sale, in un modo qualunque purché costante.",
                "**Perché non Pearson.** Pearson assume che i numeri stiano su una scala "
                "dove la distanza fra loro significhi qualcosa, e dà il meglio quando la "
                "relazione è una retta e la dispersione è grossomodo normale. Una scala "
                "1–5 è ordinata, ma nessuno può dire che il passo da 4 a 5 sia lungo "
                "quanto quello da 1 a 2, e con qualche decina di risposte non c'è modo "
                "di verificare nemmeno il resto.",
                "**Cosa costa.** Lavorare sui ranghi butta via l'ampiezza dei salti, "
                "quindi una relazione può essere perfetta qui ed essere curva nei dati. "
                "E rho risponde solo sull'ordine: non si accorge di un andamento che "
                "prima sale e poi torna giù.",
            ],
        },
        "mannwhitney": {
            "title": "U di Mann-Whitney",
            "body": [
                "Due gruppi, un insieme di risposte. Ogni risposta viene messa in rango "
                "contro tutte le altre, e U conta quante volte un valore di un gruppo "
                "sta sopra un valore dell'altro.",
                "**Perché non il test t.** Il t confronta medie e si appoggia sul fatto "
                "che ciascun gruppo sia grossomodo normale. Le scale di valutazione sono "
                "limitate ai due estremi, a gradini, e di solito ammassate verso un lato; "
                "con venti o trenta persone quell'assunzione non la puoi verificare "
                "abbastanza bene da fidartene. Ai ranghi non serve.",
                "**Cosa costa.** La domanda a cui risponde non è «le medie sono diverse» "
                "ma «un gruppo tende a stare sopra l'altro». Se i due gruppi hanno "
                "dispersioni molto diverse, può reagire a quello invece che allo "
                "spostamento che avevi in mente. Le mediane accanto al risultato sono lì "
                "per questo.",
            ],
        },
        "kruskal": {
            "title": "H di Kruskal-Wallis",
            "body": [
                "Mann-Whitney con più di due gruppi: la stessa messa in rango, distesa "
                "su tre o più, a chiedere se vengono tutti dallo stesso posto.",
                "**Perché non l'ANOVA.** L'analisi della varianza a una via assume gruppi "
                "normali con varianze simili e confronta medie; valgono le stesse "
                "obiezioni del test t, e più forti quando i gruppi sono piccoli.",
                "**Cosa costa.** Un p piccolo dice che almeno un gruppo si discosta "
                "dagli altri, non quale. Scoprirlo vuol dire confrontare le coppie, e "
                "ogni confronto è un'altra occasione perché qualcosa sembri vero per "
                "caso: per questo il pannello non li offre.",
            ],
        },
        "chi2": {
            "title": "Chi quadro di indipendenza",
            "body": [
                "I conteggi della tabella vengono confrontati con quelli che ti "
                "aspetteresti se le due domande non c'entrassero niente l'una con "
                "l'altra. Più la tabella è lontana da lì, più la statistica è grande.",
                "**Cosa assume.** Che i conteggi attesi non siano minuscoli. Sotto il "
                "cinque per cella l'approssimazione comincia a scivolare, e con una "
                "survey piccola e una domanda con molte opzioni capita facilmente — per "
                "questo il pannello quelle celle le conta e te lo dice.",
                "**Cosa costa.** Dice che le due non sono indipendenti, e niente su come "
                "o quanto. A quello servono la V di Cramér accanto e la tabella qui "
                "sotto: la forma dell'associazione sta nelle celle, non nel p.",
            ],
        },
        "fisher": {
            "title": "Test esatto di Fisher",
            "body": [
                "Su una tabella due per due la probabilità esatta di ogni tabella con "
                "gli stessi totali di riga e colonna si può semplicemente sommare, e il "
                "p è la quota di quelle sbilanciate almeno quanto quella osservata.",
                "**Perché si usa qui.** Nessuna approssimazione, quindi niente che possa "
                "sbagliare quando i conteggi sono pochi — che è esattamente il caso in "
                "cui il chi quadro diventa inaffidabile. Su una 2×2 calcolarlo non costa "
                "niente, quindi il pannello lo preferisce sempre.",
                "**Cosa costa.** Tratta i margini della tabella come dati, che è un "
                "modello del campionamento più rigido di quello che le survey seguono "
                "davvero, e per questo tende a essere un po' conservativo.",
            ],
        },
    },
}


def help_for(locale: str) -> dict:
    """Help topics in the viewer's language, each falling back to English on
    its own rather than the whole set at once."""
    out = dict(HELP["en"])
    out.update(HELP.get(locale, {}))
    return out

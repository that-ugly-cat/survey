"""The participant-facing notice: what this platform is, and what happens to an answer.

One text, four languages, reachable from the header of every questionnaire. It
is deliberately *not* a privacy policy: it explains how the tool works, in the
words a person actually reads. The purpose of the research,
its legal basis and its ethics approval belong to the study, and the notice
says so rather than pretending to cover them.

Every claim here is checkable in this repository or on the machine: no cookies
and no browser storage (grep the templates), nothing loaded from a third party
(everything the questionnaire needs is vendored in `static/`), the respondent's
address not recorded next to the answers (the app logs the proxy), cells under
five masked with complementary suppression (`aggregate.py`), and open answers
never leaving the owner's view (`OPEN_CELLS`, `for_audience`). If one of those
stops being true, this file is wrong and has to change with it.
"""

LANGS = ("en", "de", "fr", "it")

NOTICE = {
    "it": {
        "link": "Come funziona questa piattaforma",
        "close": "Chiudi",
        "title": "Come funziona questa piattaforma, e cosa succede alle tue risposte",
        "sections": [
            {"h": "Che cos'è?", "p": [
                "Survey è uno strumento sviluppato e ospitato da un gruppo di ricerca "
                "(ITE Lab, Institute of Biomedical Ethics and History of Medicine, "
                "University of Zurich), non un servizio commerciale. Le risposte non "
                "passano da Qualtrics, da Google o da nessun'altra azienda: restano su un "
                "server in Svizzera, gestito direttamente dal gruppo. Il codice è aperto e "
                "chiunque può controllarlo; le risposte sono al sicuro. Non ci sono "
                "pubblicità, non c'è profilazione, non vendiamo i tuoi dati.",
            ]},
            {"h": "Cosa viene registrato", "p": [
                "Le risposte che dai, così come le scrivi. Insieme a quelle, tre cose "
                "tecniche: quanto tempo passi su ciascuna pagina, quale versione del "
                "questionario ti è capitata se lo studio ne prevede più di una, e la data "
                "di invio. Non ti viene chiesto di registrarti e non serve un account. Il "
                "tuo nome, la tua email o qualunque altro dato che ti identifichi esistono "
                "solo se una domanda te li chiede esplicitamente, e in quel caso lo vedi, "
                "perché sei tu a scriverli.",
                "Se sei arrivato tramite una società di panel, il link che hai aperto porta "
                "un codice che serve a farti accreditare la partecipazione. Quel codice non "
                "dice a noi chi sei, ma la società che te l'ha dato può risalire a te.",
            ]},
            {"h": "Le risposte aperte sono testo libero", "p": [
                "Quello che scrivi in una casella di testo viene salvato esattamente come "
                "l'hai scritto. Se ci metti dentro un nome, un luogo o una data, quelli "
                "restano lì. Vale la pena scrivere solo ciò che sei disposto a lasciare "
                "scritto.",
            ]},
            {"h": "Chi vede cosa?", "p": [
                "Le risposte singole le vede solo chi conduce lo studio. Se lo studio "
                "pubblica una pagina di risultati, lì compaiono soltanto conteggi "
                "aggregati: nessuna risposta individuale, e le caselle con meno di cinque "
                "persone restano nascoste, perché su numeri piccoli un incrocio può bastare "
                "a riconoscere qualcuno. Le risposte aperte non compaiono mai su una pagina "
                "pubblica, in nessun caso.",
            ]},
            {"h": "Cosa non c'è", "p": [
                "Questo questionario non imposta cookie e non salva niente nel tuo browser. "
                "Non ci sono strumenti di analisi, pulsanti social o tracciatori di terze "
                "parti, e tutto quello che la pagina carica arriva dallo stesso server: "
                "nessun altro sito viene contattato mentre rispondi. Come ogni sito "
                "raggiungibile da internet, la connessione passa attraverso un servizio che "
                "la instrada e che vede l'indirizzo da cui arrivi; il questionario, dal "
                "canto suo, quell'indirizzo non lo registra.",
            ]},
            {"h": "Posso cancellare una risposta?", "p": [
                "Qui c'è una conseguenza scomoda dell'anonimato: se una risposta non è "
                "collegata a te da niente, dopo l'invio non c'è modo di ritrovarla per "
                "cancellarla. Se hai un ripensamento, scrivi a chi conduce lo studio il "
                "prima possibile e con qualche riferimento (giorno e ora, per esempio) "
                "così che ci sia una possibilità di individuarla.",
            ]},
            {"h": "Chi è responsabile?", "p": [
                "Dello studio risponde chi lo conduce, e i suoi recapiti stanno nel foglio "
                "informativo che hai ricevuto insieme all'invito, o all'inizio del "
                "questionario. Questa pagina spiega come funziona lo strumento; le "
                "finalità della ricerca, la base giuridica e l'approvazione etica "
                "riguardano lo studio e stanno lì.",
            ]},
            {"h": "Quanto a lungo conservate i dati?", "p": [
                "Dipende dallo studio, e la durata è scritta nel foglio informativo. La "
                "regola generale della ricerca è che i dati si conservano per diversi anni "
                "dopo la pubblicazione, perché un risultato deve restare verificabile; "
                "quello che resta, però, sono risposte che non portano il tuo nome.",
            ]},
        ],
        "contact": "Per domande tecniche sullo strumento: giovanni.spitale@ibme.uzh.ch",
    },
    "en": {
        "link": "How this platform works",
        "close": "Close",
        "title": "How this platform works, and what happens to your answers",
        "sections": [
            {"h": "What is this?", "p": [
                "Survey is a tool built and hosted by a research group (ITE Lab, Institute "
                "of Biomedical Ethics and History of Medicine, University of Zurich), not a "
                "commercial service. Your answers do not travel through Qualtrics, Google "
                "or any other company: they stay on a server in Switzerland that the group "
                "runs itself. The code is open and anyone can check it; your answers are "
                "safe here. There is no advertising, no profiling, and we do not sell your "
                "data.",
            ]},
            {"h": "What gets recorded", "p": [
                "The answers you give, exactly as you write them. Alongside them, three "
                "technical things: how long you spend on each page, which version of the "
                "questionnaire you were given if the study runs more than one, and the date "
                "you submitted. You are not asked to register and no account is needed. "
                "Your name, your email or anything else that identifies you exist only if a "
                "question asks for them explicitly, and then you can see it, because you "
                "are the one typing them.",
                "If you arrived through a panel company, the link you opened carries a code "
                "that lets your participation be credited. That code does not tell us who "
                "you are, but the company that issued it can trace it back to you.",
            ]},
            {"h": "Open answers are free text", "p": [
                "Whatever you type into a text box is saved exactly as you wrote it. If you "
                "put a name, a place or a date in it, they stay there. Only write what you "
                "are willing to leave written.",
            ]},
            {"h": "Who sees what?", "p": [
                "Individual responses are seen only by the people running the study. If the "
                "study publishes a results page, only aggregate counts appear there: no "
                "individual response, and cells with fewer than five people stay hidden, "
                "because with small numbers a cross-tabulation can be enough to recognise "
                "someone. Open answers never appear on a public page, under any "
                "circumstances.",
            ]},
            {"h": "What is not here", "p": [
                "This questionnaire sets no cookies and stores nothing in your browser. "
                "There are no analytics tools, no social buttons and no third-party "
                "trackers, and everything the page loads comes from the same server: no "
                "other site is contacted while you answer. Like any site reachable from the "
                "internet, your connection passes through a service that routes it and that "
                "sees the address you come from; the questionnaire itself does not record "
                "that address.",
            ]},
            {"h": "Can I delete a response?", "p": [
                "Here is an awkward consequence of anonymity: if a response is not linked "
                "to you by anything, there is no way to find it again after submission in "
                "order to delete it. If you change your mind, write to the people running "
                "the study as soon as possible and with some reference (the day and time, "
                "for instance) so that there is a chance of identifying it.",
            ]},
            {"h": "Who is responsible?", "p": [
                "The study is the responsibility of the people running it, and their "
                "contact details are in the information sheet you received with the "
                "invitation, or at the start of the questionnaire. This page explains how "
                "the tool works; the purpose of the research, its legal basis and its "
                "ethics approval belong to the study and are set out there.",
            ]},
            {"h": "How long do you keep the data?", "p": [
                "It depends on the study, and the period is stated in the information "
                "sheet. The general rule in research is that data are kept for several "
                "years after publication, because a result has to remain verifiable; what "
                "is kept, though, are answers that do not carry your name.",
            ]},
        ],
        "contact": "Technical questions about the tool: giovanni.spitale@ibme.uzh.ch",
    },
    "de": {
        "link": "Wie diese Plattform funktioniert",
        "close": "Schliessen",
        "title": "Wie diese Plattform funktioniert und was mit Ihren Antworten geschieht",
        "sections": [
            {"h": "Was ist das?", "p": [
                "Survey ist ein Werkzeug, das eine Forschungsgruppe (ITE Lab, Institute of "
                "Biomedical Ethics and History of Medicine, Universität Zürich) selbst "
                "entwickelt und betreibt, kein kommerzieller Dienst. Ihre Antworten laufen "
                "nicht über Qualtrics, Google oder ein anderes Unternehmen: Sie bleiben auf "
                "einem Server in der Schweiz, den die Gruppe selbst verwaltet. Der Code ist "
                "offen und kann von allen überprüft werden; Ihre Antworten sind hier "
                "sicher. Es gibt keine Werbung, kein Profiling, und wir verkaufen Ihre "
                "Daten nicht.",
            ]},
            {"h": "Was gespeichert wird", "p": [
                "Die Antworten, die Sie geben, genau so, wie Sie sie schreiben. Dazu drei "
                "technische Angaben: wie lange Sie auf jeder Seite bleiben, welche Fassung "
                "des Fragebogens Sie erhalten haben, falls die Studie mehrere vorsieht, und "
                "das Datum der Übermittlung. Sie müssen sich nicht registrieren, ein Konto "
                "ist nicht nötig. Ihr Name, Ihre E-Mail-Adresse oder andere Angaben, die "
                "Sie identifizieren, kommen nur vor, wenn eine Frage ausdrücklich danach "
                "fragt, und dann sehen Sie es, weil Sie sie selbst eintippen.",
                "Wenn Sie über ein Panel-Unternehmen hierhergekommen sind, enthält der "
                "geöffnete Link einen Code, mit dem Ihnen die Teilnahme gutgeschrieben "
                "wird. Dieser Code sagt uns nicht, wer Sie sind, aber das Unternehmen, das "
                "ihn vergeben hat, kann ihn zu Ihnen zurückverfolgen.",
            ]},
            {"h": "Offene Antworten sind freier Text", "p": [
                "Was Sie in ein Textfeld schreiben, wird genau so gespeichert, wie Sie es "
                "geschrieben haben. Wenn ein Name, ein Ort oder ein Datum darin steht, "
                "bleibt es dort stehen. Schreiben Sie nur, was Sie geschrieben stehen "
                "lassen möchten.",
            ]},
            {"h": "Wer sieht was?", "p": [
                "Einzelne Antworten sehen nur die Personen, die die Studie durchführen. "
                "Veröffentlicht die Studie eine Ergebnisseite, erscheinen dort "
                "ausschliesslich aggregierte Zahlen: keine einzelne Antwort, und Felder mit "
                "weniger als fünf Personen bleiben verborgen, weil bei kleinen Zahlen "
                "schon eine Kreuztabelle genügen kann, um jemanden zu erkennen. Offene "
                "Antworten erscheinen in keinem Fall auf einer öffentlichen Seite.",
            ]},
            {"h": "Was es hier nicht gibt", "p": [
                "Dieser Fragebogen setzt keine Cookies und speichert nichts in Ihrem "
                "Browser. Es gibt keine Analysewerkzeuge, keine Social-Media-Schaltflächen "
                "und keine Tracker Dritter, und alles, was die Seite lädt, kommt vom selben "
                "Server: Während Sie antworten, wird keine andere Website kontaktiert. Wie "
                "bei jeder über das Internet erreichbaren Seite läuft Ihre Verbindung "
                "über einen Dienst, der sie weiterleitet und dabei die Adresse sieht, von "
                "der Sie kommen; der Fragebogen selbst speichert diese Adresse nicht.",
            ]},
            {"h": "Kann ich eine Antwort löschen?", "p": [
                "Hier zeigt sich eine unbequeme Folge der Anonymität: Wenn eine Antwort "
                "durch nichts mit Ihnen verbunden ist, lässt sie sich nach dem Absenden "
                "nicht wiederfinden und damit auch nicht löschen. Wenn Sie es sich anders "
                "überlegen, schreiben Sie so bald wie möglich an die Personen, die die "
                "Studie durchführen, und nennen Sie einen Anhaltspunkt (etwa Tag und "
                "Uhrzeit), damit eine Chance besteht, sie zu finden.",
            ]},
            {"h": "Wer ist verantwortlich?", "p": [
                "Für die Studie verantwortlich sind die Personen, die sie durchführen; "
                "ihre Kontaktdaten stehen im Informationsblatt, das Sie mit der Einladung "
                "erhalten haben, oder am Anfang des Fragebogens. Diese Seite erklärt, wie "
                "das Werkzeug funktioniert; Zweck der Forschung, Rechtsgrundlage und "
                "Ethikgenehmigung betreffen die Studie und stehen dort.",
            ]},
            {"h": "Wie lange bewahren Sie die Daten auf?", "p": [
                "Das hängt von der Studie ab, und die Dauer steht im Informationsblatt. "
                "Die allgemeine Regel in der Forschung lautet, dass Daten nach der "
                "Veröffentlichung mehrere Jahre aufbewahrt werden, weil ein Ergebnis "
                "überprüfbar bleiben muss; aufbewahrt werden dabei Antworten, die Ihren "
                "Namen nicht tragen.",
            ]},
        ],
        "contact": "Technische Fragen zum Werkzeug: giovanni.spitale@ibme.uzh.ch",
    },
    "fr": {
        "link": "Comment fonctionne cette plateforme",
        "close": "Fermer",
        "title": "Comment fonctionne cette plateforme, et ce qu'il advient de vos réponses",
        "sections": [
            {"h": "Qu'est-ce que c'est ?", "p": [
                "Survey est un outil développé et hébergé par un groupe de recherche (ITE "
                "Lab, Institute of Biomedical Ethics and History of Medicine, Université de "
                "Zurich), et non un service commercial. Vos réponses ne passent ni par "
                "Qualtrics, ni par Google, ni par aucune autre entreprise : elles restent "
                "sur un serveur situé en Suisse, que le groupe gère lui-même. Le code est "
                "ouvert et chacun peut le vérifier ; vos réponses sont en sécurité. Il n'y "
                "a pas de publicité, pas de profilage, et nous ne vendons pas vos "
                "données.",
            ]},
            {"h": "Ce qui est enregistré", "p": [
                "Les réponses que vous donnez, telles que vous les écrivez. Avec elles, "
                "trois éléments techniques : le temps que vous passez sur chaque page, la "
                "version du questionnaire qui vous a été attribuée si l'étude en prévoit "
                "plusieurs, et la date d'envoi. Aucune inscription ne vous est demandée et "
                "aucun compte n'est nécessaire. Votre nom, votre adresse e-mail ou toute "
                "autre donnée qui vous identifie n'existent que si une question les demande "
                "explicitement, et dans ce cas vous le voyez, puisque c'est vous qui les "
                "écrivez.",
                "Si vous êtes arrivé par une société de panel, le lien que vous avez ouvert "
                "contient un code qui permet de créditer votre participation. Ce code ne "
                "nous dit pas qui vous êtes, mais la société qui vous l'a remis peut "
                "remonter jusqu'à vous.",
            ]},
            {"h": "Les réponses ouvertes sont du texte libre", "p": [
                "Ce que vous écrivez dans un champ de texte est enregistré exactement tel "
                "que vous l'avez écrit. Si vous y mettez un nom, un lieu ou une date, ils y "
                "restent. N'écrivez que ce que vous acceptez de laisser écrit.",
            ]},
            {"h": "Qui voit quoi ?", "p": [
                "Les réponses individuelles ne sont vues que par les personnes qui mènent "
                "l'étude. Si l'étude publie une page de résultats, seuls des décomptes "
                "agrégés y figurent : aucune réponse individuelle, et les cases comptant "
                "moins de cinq personnes restent masquées, parce que sur de petits nombres "
                "un croisement peut suffire à reconnaître quelqu'un. Les réponses ouvertes "
                "n'apparaissent jamais sur une page publique, en aucun cas.",
            ]},
            {"h": "Ce qu'il n'y a pas", "p": [
                "Ce questionnaire ne dépose aucun cookie et n'enregistre rien dans votre "
                "navigateur. Il n'y a ni outils de mesure d'audience, ni boutons de réseaux "
                "sociaux, ni traceurs tiers, et tout ce que la page charge provient du même "
                "serveur : aucun autre site n'est contacté pendant que vous répondez. "
                "Comme pour tout site accessible depuis internet, votre connexion transite "
                "par un service qui l'achemine et qui voit l'adresse d'où vous venez ; le "
                "questionnaire, lui, n'enregistre pas cette adresse.",
            ]},
            {"h": "Puis-je supprimer une réponse ?", "p": [
                "Il y a ici une conséquence inconfortable de l'anonymat : si rien ne relie "
                "une réponse à vous, il n'y a aucun moyen de la retrouver après l'envoi "
                "pour la supprimer. Si vous changez d'avis, écrivez dès que possible aux "
                "personnes qui mènent l'étude, avec un repère (le jour et l'heure, par "
                "exemple) afin qu'il y ait une chance de l'identifier.",
            ]},
            {"h": "Qui est responsable ?", "p": [
                "L'étude relève de la responsabilité de celles et ceux qui la mènent, et "
                "leurs coordonnées figurent dans la feuille d'information que vous avez "
                "reçue avec l'invitation, ou au début du questionnaire. Cette page explique "
                "comment fonctionne l'outil ; les finalités de la recherche, la base "
                "légale et l'approbation éthique concernent l'étude et sont indiquées "
                "là.",
            ]},
            {"h": "Combien de temps conservez-vous les données ?", "p": [
                "Cela dépend de l'étude, et la durée est indiquée dans la feuille "
                "d'information. La règle générale dans la recherche est que les données "
                "sont conservées plusieurs années après la publication, parce qu'un "
                "résultat doit rester vérifiable ; ce qui est conservé, cependant, ce sont "
                "des réponses qui ne portent pas votre nom.",
            ]},
        ],
        "contact": "Questions techniques sur l'outil : giovanni.spitale@ibme.uzh.ch",
    },
}


def notice_for(locale: str = "en") -> dict:
    """The notice in one language, falling back to English."""
    return NOTICE.get(locale, NOTICE["en"])

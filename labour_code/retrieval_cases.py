# labour_code/retrieval_cases.py: the answer key for search. Run it to check every expected article.
import json

# (question, language, expected article number)
CASES = [
    ("How many days of paid leave do I earn per month?", "en", "L3141-3"),
    ("Combien de jours de congés payés j'acquiers par mois ?", "fr", "L3141-3"),
    # the exact question the agent failed on: L3141-3 ranked 10th, the agent only gets 8
    ("What does French law say about how many days of paid leave I earn?", "en", "L3141-3"),
    ("Que dit le Code du travail sur le nombre de jours de congés payés que j'acquiers ?", "fr", "L3141-3"),
    ("Is 1 May a public holiday?", "en", "L3133-4"),
    ("Le 1er mai est-il un jour férié chômé ?", "fr", "L3133-4"),
    ("How many hours of rest must I have between two working days?", "en", "L3131-1"),
    ("Quel est le repos quotidien minimum entre deux journées de travail ?", "fr", "L3131-1"),
    ("How long is the weekly rest period?", "en", "L3132-2"),
    ("Quelle est la durée du repos hebdomadaire ?", "fr", "L3132-2"),
    ("What is the legal working week in France?", "en", "L3121-27"),
    ("Quelle est la durée légale du travail par semaine ?", "fr", "L3121-27"),
    ("How long is maternity leave?", "en", "L1225-17"),
    ("Quelle est la durée du congé de maternité ?", "fr", "L1225-17"),
    ("How long can a trial period last on a permanent contract?", "en", "L1221-19"),
    ("Quelle est la durée maximale de la période d'essai en CDI ?", "fr", "L1221-19"),
    ("Is workplace bullying forbidden by law?", "en", "L1152-1"),
    ("Le harcèlement moral au travail est-il interdit ?", "fr", "L1152-1"),
        # ---------- extended set (1 Nov): 16 more rules, each checked against the article text ----------
    ("How much notice does my employer have to give me if I'm dismissed?", "en", "L1234-1"),
    ("Quel préavis dois-je avoir si je suis licencié ?", "fr", "L1234-1"),
    ("Am I entitled to severance pay if I'm fired after a year on a permanent contract?", "en", "L1234-9"),
    ("Ai-je droit à une indemnité si on me licencie après un an en CDI ?", "fr", "L1234-9"),
    ("What is the maximum number of hours I can work in one day?", "en", "L3121-18"),
    ("Combien d'heures maximum puis-je travailler dans une journée ?", "fr", "L3121-18"),
    ("What is the absolute maximum I can work in a single week?", "en", "L3121-20"),
    ("Quel est le maximum d'heures de travail sur une semaine ?", "fr", "L3121-20"),
    ("Do I get a break if I work six hours in a row?", "en", "L3121-16"),
    ("Ai-je droit à une pause après six heures de travail ?", "fr", "L3121-16"),
    ("Which days are public holidays in France?", "en", "L3133-1"),
    ("Quels sont les jours fériés en France ?", "fr", "L3133-1"),
    ("How long is paternity leave when my baby is born?", "en", "L1225-35"),
    ("Combien de jours dure le congé paternité ?", "fr", "L1225-35"),
    ("How much extra am I paid for overtime hours?", "en", "L3121-36"),
    ("De combien sont majorées les heures supplémentaires ?", "fr", "L3121-36"),
    ("Can my trial period be extended, and up to how long?", "en", "L1221-21"),
    ("Ma période d'essai peut-elle être renouvelée, et jusqu'à quelle durée ?", "fr", "L1221-21"),
    ("During which months do I take my paid holidays?", "en", "L3141-13"),
    ("À quelle période de l'année prend-on ses congés payés ?", "fr", "L3141-13"),
    ("Can I be treated differently at work because of my origin or religion?", "en", "L1132-1"),
    ("Un employeur peut-il me sanctionner à cause de mon origine ou de ma religion ?", "fr", "L1132-1"),
    ("What counts as sexual harassment at work?", "en", "L1153-1"),
    ("Qu'est-ce que le harcèlement sexuel au travail ?", "fr", "L1153-1"),
    ("How many days off do I get when I get married?", "en", "L3142-4"),
    ("Combien de jours de congé ai-je pour mon mariage ?", "fr", "L3142-4"),
    ("Is Sunday the normal day of rest?", "en", "L3132-3"),
    ("Le repos hebdomadaire doit-il être le dimanche ?", "fr", "L3132-3"),
    ("Is my employer responsible for protecting my health and safety?", "en", "L4121-1"),
    ("L'employeur doit-il protéger la santé et la sécurité des salariés ?", "fr", "L4121-1"),
    ("What does the law say about working from home?", "en", "L1222-9"),
    ("Que dit la loi sur le télétravail ?", "fr", "L1222-9"),
]



# expected article: "L1471-1|L1235-7" means either one counts as correct

# held-out set (1 Nov): rules the reranker prompt was NEVER tuned on. Don't change the reranker to fix
# misses here; if this set drops, the 50/50 on CASES was overfitting.
HOLDOUT = [
    ("What hours count as night work?", "en", "L3122-2"),
    ("À partir de quelle heure le travail est-il considéré comme du travail de nuit ?", "fr", "L3122-2"),
    ("How long can a night worker work in one shift?", "en", "L3122-6"),
    ("Combien d'heures maximum un travailleur de nuit peut-il faire par jour ?", "fr", "L3122-6"),
    ("Can my employer and I agree to end my contract by mutual agreement?", "en", "L1237-11"),
    ("Peut-on mettre fin au CDI d'un commun accord avec l'employeur ?", "fr", "L1237-11"),
    ("Does my employer have to meet me before firing me?", "en", "L1232-2"),
    ("Mon employeur doit-il me convoquer à un entretien avant de me licencier ?", "fr", "L1232-2"),
    ("Can I be punished for reporting bullying I witnessed at work?", "en", "L1152-2"),
    ("Puis-je être sanctionné pour avoir témoigné d'un harcèlement moral ?", "fr", "L1152-2"),
    ("Can I take parental leave after my maternity leave ends?", "en", "L1225-47"),
    ("Ai-je droit à un congé parental après mon congé maternité ?", "fr", "L1225-47"),
    ("Can I be fired while I'm off work after a workplace accident?", "en", "L1226-9"),
    ("Peut-on me licencier pendant mon arrêt pour accident du travail ?", "fr", "L1226-9"),
    ("Will I be paid for my unused holiday days when I leave the company?", "en", "L3141-28"),
    ("Mes congés non pris sont-ils payés quand je quitte l'entreprise ?", "fr", "L3141-28"),
    ("How long do I have to take my employer to court?", "en", "L1471-1"),
    # both give 12 months: L1471-1 for any dismissal, L1235-7 for an economic dismissal
    ("Dans quel délai puis-je contester mon licenciement devant les prud'hommes ?", "fr", "L1471-1|L1235-7"),
    ("Can a company refuse to hire me because I'm pregnant?", "en", "L1225-1"),
    ("Un employeur peut-il refuser de m'embaucher parce que je suis enceinte ?", "fr", "L1225-1"),
]


# situation-style questions (2 Nov): how employees really ask ("my manager says..."). The agent has to EXTRACT
# the legal question, and its rewording can lose the key words (the real bug: "20 days of paid holiday" -> L3141-3
# missing from the top 20). Accepted alternatives were decided BEFORE the first run.
SITUATIONS = [
    ("My manager says I only get 20 days of paid holiday a year. Is that legal?", "en", "L3141-3"),
    ("Mon responsable dit que je n'ai droit qu'à 20 jours de congés payés par an. Est-ce légal ?", "fr", "L3141-3"),
    ("My boss wants me to work 12 hours tomorrow to finish a project. Can he ask that?", "en", "L3121-18"),
    ("Mon patron veut que je travaille 12 heures demain pour finir un projet. Il a le droit ?", "fr", "L3121-18"),
    ("I did 6 hours of overtime last week but they were paid at the normal rate. Is that right?", "en", "L3121-36"),
    ("J'ai fait 6 heures supplémentaires la semaine dernière mais elles sont payées au taux normal. C'est normal ?", "fr", "L3121-36"),
    # L3142-4 gives 3 days of birth leave, but only L1225-35 (25 days of paternity leave) shows the manager is wrong
    ("My wife is due next month and my manager says I can only take 3 days off for the birth. Is that true?", "en", "L1225-35"),
    ("Ma femme accouche le mois prochain et mon responsable dit que je n'ai droit qu'à 3 jours. C'est vrai ?", "fr", "L1225-35"),
    ("I'm resigning next month and I still have 8 days of holiday left. Will I lose them?", "en", "L3141-28"),
    ("Je démissionne le mois prochain et il me reste 8 jours de congés. Est-ce que je les perds ?", "fr", "L3141-28"),
    ("We work 7-hour shifts without any break at all. Is that allowed?", "en", "L3121-16"),
    ("On fait des journées de 7 heures sans aucune pause. Est-ce autorisé ?", "fr", "L3121-16"),
    # both say a dismissal needs a prior meeting: L1232-2 (the meeting), L1232-6 (letter only 2 days after it)
    ("My manager emailed me that I'm fired from tomorrow, without any meeting. Can he do that?", "en", "L1232-2|L1232-6"),
    ("Mon responsable m'a écrit que je suis licencié à partir de demain, sans entretien. Il peut faire ça ?", "fr", "L1232-2|L1232-6"),
]

if __name__ == "__main__":
    texts = {}
    with open("labour_code/chunks.jsonl", encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            texts.setdefault(c["num"], c["text"])   # first piece is enough to check

    missing = 0
    for num in dict.fromkeys(a for _, _, n in CASES + HOLDOUT + SITUATIONS for a in n.split("|")):   # each article once
        if num not in texts:
            missing += 1
            print(f"MISSING {num}: not in the in-force chunks!\n")
        else:
            print(f"{num}: {texts[num][:300]}\n")
    print(f"{len(CASES)} + {len(HOLDOUT)} held-out questions, {missing} expected articles missing")
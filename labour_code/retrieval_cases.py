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
]


if __name__ == "__main__":
    texts = {}
    with open("labour_code/chunks.jsonl", encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            texts.setdefault(c["num"], c["text"])   # first piece is enough to check

    missing = 0
    for num in dict.fromkeys(n for _, _, n in CASES):   # each article once
        if num not in texts:
            missing += 1
            print(f"MISSING {num}: not in the in-force chunks!\n")
        else:
            print(f"{num}: {texts[num][:300]}\n")
    print(f"{len(CASES)} questions, {missing} expected articles missing")
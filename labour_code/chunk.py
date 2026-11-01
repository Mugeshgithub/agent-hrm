# labour_code/chunk.py: turn articles into search-ready chunks with metadata
import html
import json
import re
from datetime import date, datetime, timezone

MAX_CHARS = 1500          # split only articles longer than about 2,000 characters
SPLIT_ABOVE = 2000


def clean_path(path):
    # "Partie législative   &gt; Troisième partie ..." -> "Partie législative > Troisième partie ..."
    path = html.unescape(path or "")
    return re.sub(r"\s+", " ", path).strip().rstrip(".")


def to_date(value):
    # dateFin can arrive as text ("2027-01-01...") or as milliseconds since 1970; handle both
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
    return date.fromisoformat(str(value)[:10])


def in_force(article, today):
    # the CODE decides whether a law applies today, never the model
    if article["etat"] == "VIGUEUR":
        return True
    if article["etat"] == "ABROGE_DIFF":          # repealed with a deferred date
        end = to_date(article["dateFin"])
        return end is not None and end > today
    return False


def split_text(text):
    # keep short articles whole; cut long ones at line breaks (or sentences) into ~1,500-character pieces
    if len(text) <= SPLIT_ABOVE:
        return [text]
    pieces, current = [], ""
    for part in re.split(r"\n+|(?<=[.;]) ", text):     # also split after semicolons
        if current and len(current) + len(part) > MAX_CHARS:
            pieces.append(current.strip())
            current = ""
        current += part + " "
    if current.strip():
        pieces.append(current.strip())

    # last resort: a piece with no breaks at all gets cut between words
    final = []
    for piece in pieces:
        while len(piece) > MAX_CHARS:
            cut = piece.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            final.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            final.append(piece)
    return final


def main():
    today = date.today()

    # 1. read every article, keeping only those in force today
    articles, skipped = [], 0
    with open("labour_code/articles.jsonl", encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            if in_force(a, today):
                articles.append(a)
            else:
                skipped += 1

    # 2. deduplicate: one article per number
    by_num, exact_dupes, versions_resolved = {}, 0, 0
    for a in articles:
        kept = by_num.get(a["num"])
        if kept is None:
            by_num[a["num"]] = a
        elif kept["id"] == a["id"]:
            exact_dupes += 1                      # the same row twice: drop it
        else:
            versions_resolved += 1                # two real versions: keep the one that applies today
            started = [v for v in (kept, a) if (to_date(v["dateDebut"]) or date.min) <= today]
            if started:
                by_num[a["num"]] = max(started, key=lambda v: to_date(v["dateDebut"]) or date.min)

    # 3. build chunks
    chunks = []
    for a in by_num.values():
        path = clean_path(a["fullSectionsTitre"])
        pieces = split_text(a["texte"] or "")
        for i, piece in enumerate(pieces, start=1):
            part_note = f" (part {i} of {len(pieces)})" if len(pieces) > 1 else ""
            chunks.append({
                "chunk_id": f"{a['num']}#{i}",
                "num": a["num"],
                "part": "législative" if a["num"].startswith("L") else "réglementaire",
                "path": path,
                "etat": a["etat"],
                "date_fin": str(to_date(a["dateFin"])),
                "text": piece,
                # contextual chunk header: what gets embedded and keyword-searched
                "search_text": f"Article {a['num']}{part_note}. {path}. {piece}",
            })

    with open("labour_code/chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    split_articles = len({c["num"] for c in chunks if not c["chunk_id"].endswith("#1")})
    print(f"Articles in force: {len(articles)}  (skipped as no longer in force: {skipped})")
    print(f"Exact duplicates removed: {exact_dupes}   Different versions resolved: {versions_resolved}")
    print(f"Unique articles: {len(by_num)}   Chunks: {len(chunks)}")
    print(f"Articles split into several chunks: {split_articles}")
    print(f"Longest chunk: {max(len(c['text']) for c in chunks)} characters")
    example = next(c for c in chunks if c["num"] == "L3141-3")
    print(f"\nExample search_text:\n{example['search_text']}")


if __name__ == "__main__":
    main()
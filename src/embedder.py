import os
import re

import pandas as pd
import psycopg2

COLLECTION = "ao3_works"
MODEL_NAME = "all-MiniLM-L6-v2"
VECTOR_DIM = 384
BATCH_SIZE = 64

_MAX_TAG_WORDS = 6
_SURNAMES = re.compile(
    r"\b(granger|malfoy|potter|weasley|lovegood|longbottom|zabini"
    r"|parkinson|dumbledore)\b",
    re.IGNORECASE,
)
_STANDALONE_CHARACTER = re.compile(
    r"^(hermione(\s+granger)?|draco(\s+malfoy)?|harry(\s+potter)?"
    r"|ron(\s+weasley)?|ginny(\s+weasley)?)$",
    re.IGNORECASE,
)
_GARBAGE_SUMMARY = re.compile(
    r"^\s*(i\s+)?(suck|am\s+bad)\s+at\s+summar"
    r"|see\s+(inside|tags|notes)"
    r"|what\s+it\s+says"
    r"|^.{0,25}$",
    re.IGNORECASE,
)


def _clean_tags(raw: str) -> list[str]:
    if not raw:
        return []
    cleaned = []
    for tag in raw.split(" | "):
        tag = tag.strip()
        if not tag or len(tag.split()) > _MAX_TAG_WORDS:
            continue
        if _STANDALONE_CHARACTER.match(tag):
            continue
        tag = _SURNAMES.sub("", tag).strip()
        tag = re.sub(r"\s+", " ", tag).strip()
        if len(tag) < 3:
            continue
        cleaned.append(tag.replace(" ", "_").replace("/", "_"))
    return cleaned


def _clean_summary(s: str) -> str:
    if not s or _GARBAGE_SUMMARY.search(s):
        return ""
    return s.strip()


def _build_text(row) -> str:
    tags = " ".join(_clean_tags(row.get("tags") or ""))
    summary = _clean_summary(row.get("summary") or "")
    return f"{tags} {summary}".strip()


def _connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "airflow"),
        user=os.environ.get("POSTGRES_USER", "airflow"),
        password=os.environ.get("POSTGRES_PASSWORD", "airflow"),
    )


def _get_qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(
        host=os.environ.get("QDRANT_HOST", "localhost"),
        port=int(os.environ.get("QDRANT_PORT", 6333)),
    )


def _ensure_collection(client):
    from qdrant_client.models import Distance, VectorParams
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )


def load_corpus() -> pd.DataFrame:
    conn = _connect()
    df = pd.read_sql("""
        SELECT
            w.work_id, w.title, w.summary, w.rating,
            w.words, w.complete, w.kudos, w.authors,
            COALESCE(STRING_AGG(t.name, ' | '), '') AS tags
        FROM dev.works w
        LEFT JOIN dev.work_tags wt ON w.work_id = wt.work_id
        LEFT JOIN dev.tags t ON wt.tag_id = t.id
        GROUP BY w.work_id, w.title, w.summary, w.rating, w.words, w.complete, w.kudos, w.authors
        ORDER BY w.kudos DESC
    """, conn)
    conn.close()
    df["summary"] = df["summary"].fillna("")
    df["tags"] = df["tags"].fillna("")
    df["words"] = df["words"].fillna(0)
    return df


def _best_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def embed_corpus(df: pd.DataFrame | None = None):
    from sentence_transformers import SentenceTransformer
    from qdrant_client.models import PointStruct

    if df is None:
        print("Loading corpus from Postgres...")
        df = load_corpus()
        print(f"  {len(df)} works loaded.")

    client = _get_qdrant()
    _ensure_collection(client)

    device = _best_device()
    print(f"Encoding {len(df)} works with {MODEL_NAME} on {device}...")
    model = SentenceTransformer(MODEL_NAME, device=device)
    texts = [_build_text(row) for _, row in df.iterrows()]
    vectors = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    points = [
        PointStruct(
            id=int(row["work_id"]),
            vector=vectors[i].tolist(),
            payload={
                "title": row["title"] or "",
                "kudos": int(row["kudos"] or 0),
                "words": int(row["words"] or 0),
                "complete": bool(row["complete"]),
                "rating": row["rating"] or "",
                "authors": row["authors"] or "",
            },
        )
        for i, (_, row) in enumerate(df.iterrows())
    ]

    for start in range(0, len(points), BATCH_SIZE):
        client.upsert(collection_name=COLLECTION, points=points[start: start + BATCH_SIZE])

    print(f"Upserted {len(points)} vectors into Qdrant collection '{COLLECTION}'.")


def recommend_semantic(work_id: int, n: int = 10) -> pd.DataFrame:
    client = _get_qdrant()
    results = client.query_points(
        collection_name=COLLECTION,
        query=work_id,
        limit=n + 1,
        with_payload=True,
    )
    rows = [
        {"work_id": r.id, "similarity": round(r.score, 4), **r.payload}
        for r in results.points
        if r.id != work_id
    ][:n]
    return pd.DataFrame(rows)[
        ["work_id", "title", "similarity", "kudos", "words", "complete", "rating", "authors"]
    ]


if __name__ == "__main__":
    embed_corpus()

    df = load_corpus()
    seed = df.loc[df["work_id"] == 35452315].iloc[0]
    print(f"\nSeed: {seed['title']} (work_id={seed['work_id']}, kudos={seed['kudos']})")
    print("\nTop 10 semantic recommendations:")
    print(recommend_semantic(int(seed["work_id"]), n=10).to_string(index=False))

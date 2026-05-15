import os
import pickle
import re
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

# = cleaning tags =

_MAX_TAG_WORDS = 6

# Strip surnames only "POV Draco Malfoy" -> "POV Draco"
_SURNAMES = re.compile(
    r"\b(granger|malfoy|potter|weasley|lovegood|longbottom|zabini"
    r"|parkinson|dumbledore)\b",
    re.IGNORECASE,
)

# Drop tags that are purely a character name
_STANDALONE_CHARACTER = re.compile(
    r"^(hermione(\s+granger)?|draco(\s+malfoy)?|harry(\s+potter)?"
    r"|ron(\s+weasley)?|ginny(\s+weasley)?)$",
    re.IGNORECASE,
)

# Summaries that carry no content signal
_GARBAGE_SUMMARY = re.compile(
    r"^\s*(i\s+)?(suck|am\s+bad)\s+at\s+summar"
    r"|see\s+(inside|tags|notes)"
    r"|what\s+it\s+says"
    r"|^.{0,25}$",
    re.IGNORECASE,
)

# Model artifacts
MODEL_PATH = Path(__file__).parent.parent / "models"
MODEL_FILE = MODEL_PATH / "model.pkl"
INDEX_FILE = MODEL_PATH / "work_index.pkl"

# Feature weights applied before stacking (tune these)
W_TAGS = 3.0
W_SUMMARY = 1.0
W_META = 2.0


def clean_tags(raw_tags: str) -> list[str]:
    if not raw_tags:
        return []
    cleaned = []
    for tag in raw_tags.split(" | "):
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


# = load data =
def _connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "airflow"),
        user=os.environ.get("POSTGRES_USER", "airflow"),
        password=os.environ.get("POSTGRES_PASSWORD", "airflow"),
    )


def load_corpus() -> pd.DataFrame:
    conn = _connect()
    df = pd.read_sql("""
        SELECT
            w.work_id,
            w.title,
            w.summary,
            w.rating,
            w.words,
            w.complete,
            w.kudos,
            w.authors,
            COALESCE(STRING_AGG(t.name, ' | '), '') AS tags
        FROM dev.works w
        LEFT JOIN dev.work_tags wt ON w.work_id = wt.work_id
        LEFT JOIN dev.tags t ON wt.tag_id = t.id
        GROUP BY w.work_id, w.title, w.summary, w.rating, w.words, w.complete, w.kudos, w.authors
        ORDER BY w.kudos DESC
    """, conn)
    conn.close()
    df["summary"] = df["summary"].fillna("").apply(_clean_summary)
    df["words"] = df["words"].fillna(0)
    return df


# = feature engineering =

def _build_meta_matrix(df: pd.DataFrame) -> csr_matrix:
    """Encode structured metadata as sparse numeric features."""
    bins = [0, 5_000, 30_000, 100_000, np.inf]
    word_bucket = pd.cut(df["words"], bins=bins, labels=False).fillna(0).astype(float)
    # Normalise bucket to [0, 1] so it's on the same scale as binary flags
    word_bucket = word_bucket / 3.0

    meta = pd.DataFrame({
        "words_bucket": word_bucket,
        "is_complete":  df["complete"].fillna(False).astype(float),
        "rating_gen":      (df["rating"] == "General Audiences").astype(float),
        "rating_teen":     (df["rating"] == "Teen And Up Audiences").astype(float),
        "rating_mature":   (df["rating"] == "Mature").astype(float),
        "rating_explicit": (df["rating"] == "Explicit").astype(float),
    })
    return csr_matrix(meta.values)


def fit(df: pd.DataFrame) -> dict:
    tag_texts     = df["tags"].apply(lambda r: " ".join(clean_tags(r))).tolist()
    summary_texts = df["summary"].tolist()

    # Tags: CountVectorizer 
    tag_vec = CountVectorizer(
        token_pattern=r"[A-Za-z_][A-Za-z0-9_]+",
        min_df=2,
        max_features=10_000,
    )
    tag_matrix = tag_vec.fit_transform(tag_texts)

    # Summary: TF-IDF with bigrams for patterns
    summary_vec = TfidfVectorizer(
        max_features=15_000,
        ngram_range=(1, 2),
        min_df=2,
        sublinear_tf=True,
    )
    summary_matrix = summary_vec.fit_transform(summary_texts)

    meta_matrix = _build_meta_matrix(df)

    # L2-normalise each block so weights aren't dominated by dimensionality
    combined = hstack([
        normalize(tag_matrix)     * W_TAGS,
        normalize(summary_matrix) * W_SUMMARY,
        normalize(meta_matrix)    * W_META,
    ])

    return {
        "tag_vec":    tag_vec,
        "summary_vec": summary_vec,
        "matrix":     combined,
    }


# = save/load model =

def save_model(model: dict, df: pd.DataFrame):
    MODEL_PATH.mkdir(exist_ok=True)
    artifacts = {
        "tag_vec":     model["tag_vec"],
        "summary_vec": model["summary_vec"],
        "matrix":      model["matrix"],
    }
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(artifacts, f)
    index = df[["work_id", "title", "kudos", "words", "complete", "rating", "authors"]].reset_index(drop=True)
    with open(INDEX_FILE, "wb") as f:
        pickle.dump(index, f)
    print(f"Model saved ({len(df)} works, matrix shape: {model['matrix'].shape})")


def load_model() -> tuple[dict, pd.DataFrame]:
    with open(MODEL_FILE, "rb") as f:
        artifacts = pickle.load(f)
    with open(INDEX_FILE, "rb") as f:
        index = pickle.load(f)
    return artifacts, index


# = train and recommend =
def train():
    print("Loading corpus...")
    df = load_corpus()
    print(f"  {len(df)} works loaded.")
    print("Fitting combined model (tags + summary + metadata)...")
    model = fit(df)
    save_model(model, df)
    return model, df.reset_index(drop=True)


def recommend(work_id: int, n: int = 10) -> pd.DataFrame:
    artifacts, index = load_model()
    matrix = artifacts["matrix"]

    matches = index[index["work_id"] == work_id]
    if matches.empty:
        raise ValueError(f"work_id {work_id} not found. Re-run train() after new data is loaded.")

    idx = matches.index[0]
    scores = cosine_similarity(matrix[idx], matrix).flatten()
    scores[idx] = -1  # exclude seed

    top = np.argsort(scores)[::-1][:n]
    results = index.iloc[top].copy()
    results["similarity"] = scores[top].round(4)
    return results[["work_id", "title", "similarity", "kudos", "words", "complete", "rating", "authors"]]


# = checks =

def inspect_tags(work_id: int):
    conn = _connect()
    row = pd.read_sql("""
        SELECT w.title, COALESCE(STRING_AGG(t.name, ' | '), '') AS tags
        FROM dev.works w
        LEFT JOIN dev.work_tags wt ON w.work_id = wt.work_id
        LEFT JOIN dev.tags t ON wt.tag_id = t.id
        WHERE w.work_id = %s
        GROUP BY w.title
    """, conn, params=(work_id,))
    conn.close()
    if row.empty:
        print(f"work_id {work_id} not found.")
        return
    raw  = row.iloc[0]["tags"]
    cleaned = clean_tags(raw)
    print(f"\n{row.iloc[0]['title']}")
    print(f"\nRaw tags ({len(raw.split(' | '))}):")
    for t in raw.split(" | "):
        print(f"  {t}")
    print(f"\nCleaned tokens ({len(cleaned)}):")
    for t in cleaned:
        print(f"  {t}")


def top_tag_features(n: int = 30):
    """Show the highest-weight tag features in the fitted model."""
    artifacts, _ = load_model()
    vec = artifacts["tag_vec"]
    matrix = artifacts["matrix"]
    # Tag block is the first W_TAGS-weighted columns
    tag_cols = len(vec.vocabulary_)
    tag_block = matrix[:, :tag_cols]
    scores = np.asarray(tag_block.sum(axis=0)).flatten()
    top = np.argsort(scores)[::-1][:n]
    vocab_inv = {v: k for k, v in vec.vocabulary_.items()}
    print(f"\nTop {n} tag features by corpus weight:")
    for i in top:
        print(f"  {vocab_inv[i]:<40} {scores[i]:.1f}")


if __name__ == "__main__":
    inspect_tags(35452315)

    model, index = train()

    seed = index.loc[index["work_id"] == 35452315].iloc[0]
    print(f"\nSeed: {seed['title']} (work_id={seed['work_id']}, kudos={seed['kudos']})")
    print("\nTop 10 recommendations:")
    print(recommend(seed["work_id"], n=10).to_string(index=False))

    print()
    top_tag_features(30)

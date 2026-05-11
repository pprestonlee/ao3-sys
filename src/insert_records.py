import os
import psycopg2
from psycopg2.extras import execute_values

def connect_to_db():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "airflow"),
        user=os.environ.get("POSTGRES_USER", "airflow"),
        password=os.environ.get("POSTGRES_PASSWORD", "airflow"),
    )

def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS dev;

            CREATE TABLE IF NOT EXISTS dev.works (
                id           SERIAL PRIMARY KEY,
                work_id      INT NOT NULL UNIQUE,
                title        TEXT,
                publish_date DATE,
                update_date  DATE,
                bookmarks    INT,
                words        INT,
                num_chapters INT,
                complete     BOOL,
                characters   TEXT,
                comments     INT,
                hits         INT,
                kudos        INT,
                rating       TEXT,
                restricted   BOOL,
                summary      TEXT,
                collections  INT,
                series       TEXT,
                authors      TEXT,
                insert_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dev.tags (
                id   SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS dev.work_tags (
                work_id INT NOT NULL REFERENCES dev.works(work_id) ON DELETE CASCADE,
                tag_id  INT NOT NULL REFERENCES dev.tags(id) ON DELETE CASCADE,
                PRIMARY KEY (work_id, tag_id)
            );
        """)
    conn.commit()

def upsert(conn, work: dict):
    raw_tags = work.get("tags", "")
    tag_names = [t.strip() for t in raw_tags.split(",") if t.strip()] if isinstance(raw_tags, str) else raw_tags

    with conn.cursor() as cur:
        # Upsert the work row
        cur.execute("""
            INSERT INTO dev.works (work_id, title, publish_date, update_date, bookmarks, words,
                num_chapters, complete, characters, comments, hits, kudos, rating, restricted,
                summary, collections, series, authors)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (work_id) DO UPDATE SET
                title        = EXCLUDED.title,
                publish_date = EXCLUDED.publish_date,
                update_date  = EXCLUDED.update_date,
                bookmarks    = EXCLUDED.bookmarks,
                words        = EXCLUDED.words,
                num_chapters = EXCLUDED.num_chapters,
                complete     = EXCLUDED.complete,
                characters   = EXCLUDED.characters,
                comments     = EXCLUDED.comments,
                hits         = EXCLUDED.hits,
                kudos        = EXCLUDED.kudos,
                rating       = EXCLUDED.rating,
                restricted   = EXCLUDED.restricted,
                summary      = EXCLUDED.summary,
                collections  = EXCLUDED.collections,
                series       = EXCLUDED.series,
                authors      = EXCLUDED.authors
        """, (
            work.get("work_id"), work.get("title"), work.get("publish_date"),
            work.get("update_date"), work.get("bookmarks"), work.get("words"),
            work.get("num_chapters"), work.get("complete"), work.get("characters"),
            work.get("comments"), work.get("hits"), work.get("kudos"),
            work.get("rating"), work.get("restricted"), work.get("summary"),
            work.get("collections"), work.get("series"), work.get("authors"),
        ))

        if tag_names:
            # Insert any new tags
            execute_values(cur,
                "INSERT INTO dev.tags (name) VALUES %s ON CONFLICT (name) DO NOTHING",
                [(t,) for t in tag_names],
            )

            # Resolve tag ids
            cur.execute("SELECT id, name FROM dev.tags WHERE name = ANY(%s)", (tag_names,))
            tag_ids = {name: tid for tid, name in cur.fetchall()}

            # Replace work_tags for this work
            cur.execute("DELETE FROM dev.work_tags WHERE work_id = %s", (work["work_id"],))
            execute_values(cur,
                "INSERT INTO dev.work_tags (work_id, tag_id) VALUES %s",
                [(work["work_id"], tag_ids[t]) for t in tag_names if t in tag_ids],
            )

    conn.commit()


if __name__ == "__main__":
    conn = connect_to_db()
    create_schema(conn)
    print("Schema created.")

    work = {
        "work_id": 12345,
        "title": "Example Work",
        "publish_date": "2024-01-01",
        "update_date": "2024-01-02",
        "bookmarks": 100,
        "words": 5000,
        "num_chapters": 10,
        "complete": True,
        "characters": "Hermione Granger,Draco Malfoy",
        "comments": 50,
        "hits": 1000,
        "kudos": 999,
        "rating": "General Audiences",
        "restricted": False,
        "summary": "This is an example work.",
        "collections": 5,
        "tags": "Slow Burn,Enemies to Lovers,Muggle AU",
        "series": "Standalone",
        "authors": "author1",
    }

    upsert(conn, work)
    print("Upserted example work.")
    conn.close()

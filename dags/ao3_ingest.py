import json
import os
import time
from datetime import datetime, timedelta

from airflow.decorators import dag, task

BUCKET = "ao3-raw"
SLEEP_BETWEEN_REQUESTS = 2
MAX_WORKS = 5_000
REFRESH_AFTER_DAYS = 7


@dag(
    dag_id="ao3_ingest",
    schedule="@weekly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["ao3", "ingestion"],
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10)},
)
def ao3_ingest():

    @task
    def ensure_schema():
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ.get("POSTGRES_PORT", 5432)),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
        )
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
        conn.close()

    @task
    def scrape_to_minio() -> list[int]:
        import AO3
        import boto3
        from botocore.client import Config

        for attempt in range(5):
            try:
                ao3_session = AO3.Session(os.environ["AO3_USERNAME"], os.environ["AO3_PASSWORD"])
                break
            except Exception as e:
                wait = 30 * (attempt + 1)
                print(f"Login failed (attempt {attempt + 1}/5): {e}. Retrying in {wait}s...")
                time.sleep(wait)
        else:
            raise RuntimeError("AO3 login failed after 5 attempts")

        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://minio:9000"),
            aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
            aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

        existing = {b["Name"] for b in s3.list_buckets()["Buckets"]}
        if BUCKET not in existing:
            s3.create_bucket(Bucket=BUCKET)

        from datetime import timezone
        refresh_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=REFRESH_AFTER_DAYS)

        # Skip works stored within the last 7 days; re-scrape older ones so stats stay fresh
        already_stored = {
            obj["Key"].split("/")[1].replace(".json", "")
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="works/")
            for obj in page.get("Contents", [])
            if obj["LastModified"] > refresh_cutoff
        }

        scraped_ids = []
        page_num = 1

        while len(scraped_ids) < MAX_WORKS:
            search = AO3.Search(
                relationships="Hermione Granger/Draco Malfoy",
                sort_column="kudos_count",
                sort_direction="desc",
                session=ao3_session,
                page=page_num,
            )

            for attempt in range(5):
                try:
                    search.update()
                    break
                except Exception as e:
                    wait = 45 * (attempt + 1)
                    print(f"Search page {page_num} failed (attempt {attempt + 1}/5): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
            else:
                print(f"Search page {page_num} failed after 5 attempts, stopping.")
                break

            if not search.results:
                break

            for result in search.results:
                work_id = str(result.id)
                if work_id in already_stored:
                    continue

                res = None
                for attempt in range(3):
                    try:
                        res = AO3.Work(result.id, load=True, load_chapters=False, session=ao3_session)
                        break
                    except Exception as e:
                        wait = 15 * (attempt + 1)
                        print(f"Work {result.id} attempt {attempt + 1}/3 failed: {e}. Retrying in {wait}s...")
                        time.sleep(wait)
                if res is None:
                    print(f"Skipping work {result.id} after 3 failed attempts.")
                    continue

                if res.language != "English":
                    time.sleep(SLEEP_BETWEEN_REQUESTS)
                    continue

                work = {
                    "work_id":      res.id,
                    "title":        res.title,
                    "publish_date": str(res.date_published.date()),
                    "update_date":  str(res.date_updated.date()),
                    "bookmarks":    res.bookmarks,
                    "words":        res.words,
                    "num_chapters": res.nchapters,
                    "complete":     res.complete,
                    "characters":   ",".join(res.characters),
                    "comments":     res.comments,
                    "hits":         res.hits,
                    "kudos":        res.kudos,
                    "rating":       res.rating,
                    "restricted":   res.restricted,
                    "summary":      res.summary,
                    "collections":  len(res.collections),
                    "tags":         ",".join(res.tags),
                    "series":       "Standalone" if not res.series else res.series[0].name,
                    "authors":      ",".join(a.username for a in res.authors),
                }

                s3.put_object(
                    Bucket=BUCKET,
                    Key=f"works/{work_id}.json",
                    Body=json.dumps(work),
                    ContentType="application/json",
                )
                scraped_ids.append(res.id)
                print(f"Stored: {res.title} ({work_id})")
                time.sleep(SLEEP_BETWEEN_REQUESTS)

            page_num += 1
            time.sleep(SLEEP_BETWEEN_REQUESTS)

        print(f"Scraped {len(scraped_ids)} new works across {page_num - 1} pages.")
        return scraped_ids

    @task
    def load_to_postgres(_signal):
        import boto3
        import psycopg2
        from botocore.client import Config
        from psycopg2.extras import execute_values

        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://minio:9000"),
            aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
            aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

        conn = psycopg2.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ.get("POSTGRES_PORT", 5432)),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
        )

        from datetime import timezone
        refresh_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=REFRESH_AFTER_DAYS)

        # Load works that are either new (not in Postgres) or recently re-scraped (updated in MinIO)
        work_ids = {
            obj["Key"].split("/")[1].replace(".json", "")
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="works/")
            for obj in page.get("Contents", [])
            if obj["LastModified"] > refresh_cutoff
        }

        if not work_ids:
            print("No works to load.")
            conn.close()
            return

        print(f"Loading {len(work_ids)} works from MinIO into Postgres...")
        for work_id in work_ids:
            obj = s3.get_object(Bucket=BUCKET, Key=f"works/{work_id}.json")
            work = json.loads(obj["Body"].read())
            tag_names = [t.strip() for t in work.get("tags", "").split(",") if t.strip()]

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO dev.works (work_id, title, publish_date, update_date, bookmarks,
                        words, num_chapters, complete, characters, comments, hits, kudos, rating,
                        restricted, summary, collections, series, authors)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    work["work_id"], work["title"], work["publish_date"], work["update_date"],
                    work["bookmarks"], work["words"], work["num_chapters"], work["complete"],
                    work["characters"], work["comments"], work["hits"], work["kudos"],
                    work["rating"], work["restricted"], work["summary"], work["collections"],
                    work["series"], work["authors"],
                ))

                if tag_names:
                    execute_values(cur,
                        "INSERT INTO dev.tags (name) VALUES %s ON CONFLICT (name) DO NOTHING",
                        [(t,) for t in tag_names],
                    )
                    cur.execute(
                        "SELECT id, name FROM dev.tags WHERE name = ANY(%s)", (tag_names,)
                    )
                    tag_ids = {name: tid for tid, name in cur.fetchall()}

                    cur.execute("DELETE FROM dev.work_tags WHERE work_id = %s", (work["work_id"],))
                    execute_values(cur,
                        "INSERT INTO dev.work_tags (work_id, tag_id) VALUES %s",
                        [(work["work_id"], tag_ids[t]) for t in tag_names if t in tag_ids],
                    )

            conn.commit()
            print(f"Loaded: {work['title']} ({work_id})")

        conn.close()
        print(f"Loaded {len(work_ids)} works into Postgres.")

    @task
    def embed_to_qdrant(_signal):
        import sys
        sys.path.insert(0, "/opt/airflow/src")
        from embedder import embed_corpus
        embed_corpus()

    schema = ensure_schema()
    scraped = scrape_to_minio()
    schema >> scraped
    loaded = load_to_postgres(scraped)
    embed_to_qdrant(loaded)


ao3_ingest()

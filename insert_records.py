import psycopg2
import AO3

def connect_to_db():
    print("Connecting to the database...")
    try:
        conn = psycopg2.connect(
            host='localhost',
            port=5432,
            dbname='airflow',
            user='airflow',
            password='airflow'
        )
        return conn
    except psycopg2.Error as e:
        print(f"Error connecting to the database: {e}")
        raise

def create_table(conn):
    print("Creating table if not exists...")
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DROP TABLE IF EXISTS public.works;
            CREATE SCHEMA IF NOT EXISTS dev;
            CREATE TABLE IF NOT EXISTS dev.work (
                id SERIAL PRIMARY KEY,
	            work_id INT NOT NULL,
	            title TEXT,
                publish_date DATE,
                update_date DATE,
                bookmarks INT,
                words INT,
                num_chapters INT,
                complete BOOL,
                characters TEXT,
                comments INT,
                hits INT,
                kudos INT,
                rating TEXT,
                restricted BOOL,
                summary TEXT,
                collections INT,
                tags TEXT,
                series TEXT,
                authors TEXT,
                insert_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        """)
        conn.commit()
        cursor.close()
        print("Table created successfully.")
    except psycopg2.Error as e:
        print(f"Error creating table: {e}")
        raise    

def upsert(conn, work):
    print("Upserting work into the database...")
    try:
        cursor = conn.cursor()

        insert = """
            INSERT INTO dev.work (work_id, title, publish_date, update_date, bookmarks, words, num_chapters, complete, characters, comments, hits, kudos, rating, restricted, summary, collections, tags, series, authors, insert_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
            ON CONFLICT (work_id) DO UPDATE;
            """

        cursor.execute(insert, (work.get("work_id"),
                                work.get("title"),
                                work.get("publish_date"),
                                work.get("update_date"),
                                work.get("bookmarks"),
                                work.get("words"),
                                work.get("num_chapters"),
                                work.get("complete"),
                                work.get("characters"),
                                work.get("comments"),
                                work.get("hits"),
                                work.get("kudos"),
                                work.get("rating"),
                                work.get("restricted"),
                                work.get("summary"),
                                work.get("collections"),
                                work.get("tags"),
                                work.get("series"),
                                work.get("authors")))
        conn.commit()
        cursor.close()
        print("Work upserted successfully.")
    except psycopg2.Error as e:
        print(f"Error upserting work: {e}")
        raise

conn = connect_to_db()
create_table(conn)
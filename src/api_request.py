import os
import time
import AO3

def get_session() -> AO3.Session:
    username = os.environ.get("AO3_USERNAME")
    password = os.environ.get("AO3_PASSWORD")
    if not username or not password:
        raise ValueError("AO3_USERNAME and AO3_PASSWORD must be set")
    return AO3.Session(username, password)

def fetch_work(work_id: int, session: AO3.Session) -> dict | None:
    try:
        res = AO3.Work(work_id, load=True, load_chapters=False, session=session)
    except Exception as e:
        print(f"Skipping work {work_id}: {e}")
        return None

    if res.language != "English":
        return None

    return {
        "work_id":      res.id,
        "title":        res.title,
        "publish_date": res.date_published.date(),
        "update_date":  res.date_updated.date(),
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
        "authors":      ",".join(author.username for author in res.authors),
    }

def search_works(session: AO3.Session, page: int = 1) -> AO3.Search:
    search = AO3.Search(
        relationships="Hermione Granger/Draco Malfoy",
        sort_column="kudos_count",
        sort_direction="desc",
        session=session,
        page=page,
    )
    search.update()
    return search

def request(max_works: int = 20, session: AO3.Session | None = None):
    session = session or get_session()
    search = search_works(session, page=1)
    print(f"Total results: {search.total_results}")

    count = 0
    for result in search.results:
        if count >= max_works:
            break
        work = fetch_work(result.id, session)
        if work:
            print(f"{work['title']}\n{work}\n")
        count += 1
        time.sleep(2)

def request_user(username: str = "pprestonlee"):
    user = AO3.User(username)
    print(f"User: {user.username}, # of Bookmarks: {user.bio}")

if __name__ == "__main__":
    request_user()

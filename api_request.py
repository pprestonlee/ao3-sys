import AO3
from datetime import datetime

# def fetch_work(id):

def request():
    search = AO3.Search(relationships="Hermione Granger/Draco Malfoy", sort_column="kudos_count", sort_direction="desc")
    search.update()

    WORKS_PER_PAGE = 20  # Number of works fetched per page
    count = 0   # Counter for works processed
    for i in range(search.total_results):
        if count >= WORKS_PER_PAGE:
            break
            # search.page += 1
            # search.update()
        
        curr = search.results[count]
        res = AO3.Work(curr.id, load=True, load_chapters=True)
        if (res.language == "English"):
            print(f"{res.title}")
            work = {"work_id" : res.id,
                    "title" : res.title,
                    "publish_date" : res.date_published.date(),
                    "update_date" : res.date_updated.date(),
                    "bookmarks" : res.bookmarks,
                    "words" : res.words,
                    "num_chapters" : res.nchapters,
                    "complete" : res.complete,
                    "comments" : res.comments,
                    "hits" : res.hits,
                    "kudos" : res.kudos,
                    "rating" : res.rating,
                    "restricted" : res.restricted,
                    "summary" : res.summary[1:-1],
                    "collections" : len(res.collections),
                    "tags" : ','.join(res.tags),
                    "series" : "Standalone" if res.series == [] else res.series[0].name,
                    "authors": [author.username for author in res.authors]}
            print(f"{work}\n")
        count += 1  

# url = "https://archiveofourown.org/works/25634758/chapters/62228269"
# workid = AO3.utils.workid_from_url(url)
# print(f"Work ID: {workid}")
# work = AO3.Work(workid, load=True, load_chapters=False)
# print(f"Date: {work.date_published}")
# work.date_published = work.date_published.date()
# print(f"Date 2: {work.date_published}")

request()
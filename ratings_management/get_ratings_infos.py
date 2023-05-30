import pandas as pd, numpy as np

from operator import itemgetter

def get_names(x, key="name"):
    if type(x) == list and len(x) != 0:
        return ", ".join(list(map(itemgetter(key), x)))

def concat_distinct_genres(x):
    l = x["imdb_genres"].split(", ") if type(x["imdb_genres"]) == str else []
    if "Short" in l:
        l.remove("Short")    
    m = x["tmdb_genres"].split(", ") if type(x["tmdb_genres"]) == str else []
    n = l + m
    if len(n) == 0:
        return np.nan
    else:
        return ",".join(sorted(set(n)))

def merge_ratings_with_tmdb(ratings_df, data_tmdb_df, ratings_output_path):

    print("Ratings has", ratings_df.shape[0], "movies.")

    ratings_df = ratings_df.add_prefix("imdb_").rename(
        columns={"imdb_Const": "imdb_id"})
    
    ratings_df.columns = ratings_df.columns.str.strip().str.lower().str.replace(' ', '_', regex=False).str.replace('(', '', regex=False).str.replace(')', '', regex=False)
    
    data_tmdb_df = data_tmdb_df.add_prefix("tmdb_").rename(
        columns={"tmdb_imdb_id": "imdb_id"})

    ratings_df = ratings_df.merge(data_tmdb_df, on="imdb_id", how="left", validate="one_to_one")    

    ratings_dup = ratings_df[ratings_df.duplicated("imdb_id")].shape[0]
    if  ratings_dup > 0:
        print("Ratings has ", ratings_dup, " duplicates")
        raise "Ratings has duplicates, please fix it."

    # Parsing dates
    ratings_df["imdb_last_date_rated"] = pd.to_datetime(ratings_df["imdb_date_rated"])
    ratings_df["imdb_release_date"] = pd.to_datetime(ratings_df["imdb_release_date"])
    ratings_df["tmdb_release_date"] = pd.to_datetime(ratings_df["tmdb_release_date"])

    # Merging genres from imdb and tmdb
    ratings_df["tmdb_genres"] = ratings_df["tmdb_genres"].apply(get_names)    
    ratings_df["genres"] = ratings_df.apply(concat_distinct_genres,axis=1)

    # Deleting non needed columns
    ratings_df.drop(columns=["imdb_date_rated", "tmdb_video", "tmdb_status", "tmdb_poster_path"], inplace=True)

    # Rename columns
    ratings_df.rename(columns={
        "imdb_imdb_rating": "imdb_rating"
    }, inplace=True)

    print("Saving ratings data to ", ratings_output_path)
    ratings_df.to_pickle(ratings_output_path)

    return ratings_df
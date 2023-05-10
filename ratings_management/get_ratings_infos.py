from operator import itemgetter

def get_names(x, key="name"):
    return ", ".join(list(map(itemgetter(key), x)))

def concat_distinct_genres(x):
    l = x["Genres"].split(", ")
    if "Short" in l:
        l.remove("Short")    
    m = x["genres"].split(", ")
    n = l + m
    return sorted(set(n))

def merge_ratings_with_tmdb(ratings_df, data_tmdb_df, ratings_output_path):

    print("Ratings has", ratings_df.shape[0], "movies.")

    ratings_df = ratings_df.rename(columns={"Const": "imdb_id"})

    ratings_df = ratings_df.merge(data_tmdb_df, on="imdb_id", how="left")

    ratings_dup = ratings_df[ratings_df.duplicated("imdb_id")].shape[0]
    if  ratings_dup > 0:
        print("Ratings has ", ratings_dup, " duplicates")
        raise "Ratings has duplicates, please fix it."

    ratings_df.genres = ratings_df.genres.apply(get_names)
    ratings_df.Genres.fillna("", inplace=True)
    ratings_df.genres.fillna("", inplace=True)
    ratings_df["genres"] = ratings_df.apply(concat_distinct_genres,axis=1)
    ratings_df.drop(columns=["Genres"], inplace=True)

    print("Saving ratings data to ", ratings_output_path)
    ratings_df.to_pickle(ratings_output_path)

    return ratings_df
def merge_ratings_with_tmdb(ratings_df, data_tmdb_df, ratings_output_path):

    print("Ratings has", ratings_df.shape[0], "movies.")

    ratings_df = ratings_df.merge(data_tmdb_df, left_on="Const", right_on="imdb_id", how="left")

    ratings_df = ratings_df.drop(columns=["imdb_id"])

    ratings_dup = ratings_df[ratings_df.duplicated("Const")].shape[0]
    if  ratings_dup > 0:
        print("Ratings has ", ratings_dup, " duplicates")
        raise "Ratings has duplicates, please fix it."

    ratings_df.to_pickle(ratings_output_path)

    return ratings_df
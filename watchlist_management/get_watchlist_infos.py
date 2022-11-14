def update_watchlist_with_tmdb(watchlist_df, data_tmdb_df, watchlist_output_path):

    print("Watchlist has", watchlist_df.shape[0], "movies.")

    watchlist_df = watchlist_df.merge(data_tmdb_df, left_on="Const", right_on="imdb_id", how="left")

    watchlist_df = watchlist_df.drop(columns=["Position", "Created", "Modified", "Description", "imdb_id"])

    watchlist_dup = watchlist_df[watchlist_df.duplicated("Const")].shape[0]
    if  watchlist_dup > 0:
        print("Watchlist has ", watchlist_dup, " duplicates")
        raise "Whatlist has duplicates, please fix it."

    watchlist_df.to_pickle(watchlist_output_path)

    return watchlist_df
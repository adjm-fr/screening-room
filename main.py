import json
import os
import click
import datetime as dt

import pandas as pd

import tmdb_data_management.get_tmdb_data as tdm
import watchlist_management.get_watchlist_infos as wm
import get_showtimes.get_showtimes as gs

@click.command()
@click.option('--refresh_tmdb', is_flag=True, help='Do you need to refresh the TMDB database?')
def movies_management(refresh_tmdb):

    current_path = os.getcwd()

    with open(current_path + '/config.secrets.json', encoding='utf-8') as f:
        config = json.load(f)

    input_path = config["path"]["input"]
    output_path = config["path"]["output"]
    
    # Inputs loading
    data_ratings = pd.read_csv(input_path + "/ratings.csv", encoding = 'utf8')
    data_watchlist = pd.read_csv(input_path + "/WATCHLIST.csv", encoding = 'utf8')
    tmdb_data_output_path = output_path + "/data_tmdb.pkl"

    ratings_file_after_tmdb = os.path.getmtime(tmdb_data_output_path) < os.path.getmtime(input_path + "/ratings.csv")
    watchlist_file_after_tmdb = os.path.getmtime(tmdb_data_output_path) < os.path.getmtime(input_path + "/WATCHLIST.csv")

    if refresh_tmdb | ratings_file_after_tmdb | watchlist_file_after_tmdb:
        if refresh_tmdb:
            print("User asked to refresh TMDB database")
        if ratings_file_after_tmdb:
            print("TMDB databse might be deprecated as a newer ratings file is present. The TMDB database will refreshed.")
        if ratings_file_after_tmdb:
            print("TMDB databse might be deprecated as a newer watchlist file is present. The TMDB database will refreshed.")
        data_tmdb_df = tdm.refresh_tmdb_data(data_ratings, data_watchlist, tmdb_data_output_path, config)
    else:
        data_tmdb_df = pd.read_pickle(tmdb_data_output_path)
    print(data_tmdb_df.shape)

    watchlist_output_path = output_path + "watchlist_with_tmdb.pkl"
    watchlist_full_file_deprecated = os.path.getmtime(watchlist_output_path) < os.path.getmtime(input_path + "/WATCHLIST.csv")
    print(watchlist_full_file_deprecated)

    if watchlist_full_file_deprecated:
        watchlist_df = wm.update_watchlist_with_tmdb(data_watchlist, data_tmdb_df, watchlist_output_path)
    else:
        watchlist_df = pd.read_pickle(watchlist_output_path)

    with open(input_path + "theaters_allocine_urls.txt", encoding="utf-8") as file:
        theaters_urls = [line.rstrip() for line in file]

    showtimes_output_path = output_path + "showtimes.pkl"
    selenium_bin_path = output_path = config["path"]["selenium_bin"]

    #showtimes_df = gs.get_showtimes(theaters_urls, selenium_bin_path, showtimes_output_path)

if __name__ == '__main__':
    movies_management()
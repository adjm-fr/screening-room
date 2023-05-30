import json
import os
import click
import pathlib

import pandas as pd

from datetime import datetime

import tmdb_data_management.get_tmdb_data as tdm
import watchlist_management.get_watchlist_infos as wm
import ratings_management.get_ratings_infos as rm

@click.command()
@click.option('--get_tmdb', is_flag=True, help='Do you need to refresh the TMDB database?')
def movies_management(get_tmdb):

    current_path = pathlib.Path(__file__).parent.resolve()
    print(current_path)

    with open(os.path.join(current_path, 'config.secrets.json'), encoding='utf-8') as f:
        config = json.load(f)

    input_path = config["path"]["input"]
    output_path = config["path"]["output"]
    
    # Inputs loading
    ratings_input_path = os.path.join(input_path, 'ratings.csv')
    ratings_input_file_exists = os.path.exists(ratings_input_path)
    if not ratings_input_file_exists:
        raise "The ratings file doesn't exist: you need to put the ratings.csv file in this location: " + ratings_input_path
    else:
        data_ratings = pd.read_csv(ratings_input_path, encoding = 'utf8')
    
    watchlist_input_path = os.path.join(input_path, 'WATCHLIST.csv')
    watchlist_input_file_exists = os.path.exists(watchlist_input_path)
    if not watchlist_input_file_exists:
        raise "The watchlist file doesn't exist: you need to put the WATCHLIST.csv file in this location: " + watchlist_input_path
    else:
        data_watchlist = pd.read_csv(watchlist_input_path, encoding = 'utf8')
    
    # TMDB database creation or update
    tmdb_data_output_path = os.path.join(output_path, 'data_tmdb.pkl')    
    tmdb_data_file_exists = os.path.exists(tmdb_data_output_path)
    
    if not tmdb_data_file_exists:
        print("TMDB database doesn't exist. The TMDB database will be created.")
        data_tmdb_df = tdm.get_tmdb_data(data_ratings, data_watchlist, tmdb_data_output_path, config)
    else:
        ratings_file_after_tmdb = os.path.getmtime(tmdb_data_output_path) < os.path.getmtime(ratings_input_path)    
        watchlist_file_after_tmdb = os.path.getmtime(tmdb_data_output_path) < os.path.getmtime(watchlist_input_path)
        if get_tmdb | ratings_file_after_tmdb | watchlist_file_after_tmdb:
            if get_tmdb:
                print("User asked to refresh TMDB database")
            if ratings_file_after_tmdb:
                print("TMDB database might be deprecated as a newer ratings file is present. The TMDB database will be refreshed.")
            if watchlist_file_after_tmdb:
                print("TMDB database might be deprecated as a newer watchlist file is present. The TMDB database will be refreshed.")
            data_tmdb_df = tdm.get_tmdb_data(data_ratings, data_watchlist, tmdb_data_output_path, config)
        else:
            data_tmdb_df = pd.read_pickle(tmdb_data_output_path)
    print(data_tmdb_df.shape)

    print(data_tmdb_df["integration_date"].min())
    check_tmdb_integration_old = pd.to_datetime(datetime.now()) - data_tmdb_df["integration_date"].min()

    # If the minimum is lower than 9 months
    if check_tmdb_integration_old.days > 274:
        print("Some movies needs to be updated in the TMDB database (integration date greater than 9 months)")
        data_tmdb_df = tdm.refresh_tmdb_data(data_tmdb_df, tmdb_data_output_path, config)

    watchlist_output_path = os.path.join(output_path, 'watchlist_with_tmdb.pkl')
    watchlist_output_exists = os.path.exists(watchlist_output_path)

    if (not watchlist_output_exists) or (os.path.getmtime(watchlist_output_path) < os.path.getmtime(watchlist_input_path)):
        watchlist_df = wm.merge_watchlist_with_tmdb(data_watchlist, data_tmdb_df, watchlist_output_path)
    else:
        watchlist_df = pd.read_pickle(watchlist_output_path)

    ratings_output_path = os.path.join(output_path, 'ratings_with_tmdb.pkl')
    ratings_output_file_exists = os.path.exists(ratings_output_path)

    if (not ratings_output_file_exists) or (os.path.getmtime(ratings_output_path) < os.path.getmtime(ratings_input_path)):
        ratings_df = rm.merge_ratings_with_tmdb(data_ratings, data_tmdb_df, ratings_output_path)
    else:
        ratings_df = pd.read_pickle(ratings_output_path)


if __name__ == '__main__':
    movies_management()
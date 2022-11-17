import json
import os
import click

import pandas as pd

import tmdb_data_management.get_tmdb_data as tdm
import watchlist_management.get_watchlist_infos as wm

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
    if refresh_tmdb:
        data_tmdb_df = tdm.refresh_tmdb_data(data_ratings, data_watchlist, tmdb_data_output_path, config)
    else:
        data_tmdb_df = pd.read_pickle(tmdb_data_output_path)
    print(data_tmdb_df.shape)

    watchlist_output_path = output_path + "watchlist_with_tmdb.pkl"

    watchlist_df = wm.update_watchlist_with_tmdb(data_watchlist, data_tmdb_df, watchlist_output_path)

if __name__ == '__main__':
    movies_management()
from datetime import datetime

import pandas as pd
import requests


def refresh_tmdb_data(data_ratings, data_watchlist, tmdb_data_output_path, config):
    
    data = pd.concat([data_ratings, data_watchlist], ignore_index=True, join='inner')
    print(data.shape)
    try:
        data_tmdb_df = pd.read_pickle(tmdb_data_output_path)
        print(data_tmdb_df.shape)
    except Exception as e:
        print("No TMBD data already available")
        print(e)
        data_tmdb_df = pd.DataFrame()
        
    print("Number of duplicates movies in TMBD data", data_tmdb_df[data_tmdb_df.duplicated("id")].shape[0])
    
    data_tmdb_ids = data_tmdb_df["imdb_id"].unique() if data_tmdb_df.shape[0] > 0 else []
    movie_ids = data["Const"].unique()
    new_movies = list(set(movie_ids) - set(data_tmdb_ids))
    
    new_movies_number = len(new_movies)
    print("New movies to retrieve from TMBD API: ", new_movies_number)
    
    result = []
    # get english tmdb info
    # defining a params dict for the parameters to be sent to the API 
    params = {'api_key': config["tmdb"]["api_key"]}
    
    for i, movie_id in enumerate(new_movies):
        print(str((i+1)/new_movies_number * 100) + "%")
        try:
            print("Trying {}".format(movie_id))
            r = requests.get(url = config["tmdb"]["api_url"] + "/movie/" + movie_id, params = params)
            r_json = r.json()
            del r_json["adult"]
            del r_json["backdrop_path"]
            del r_json["belongs_to_collection"]
            del r_json["homepage"]
            print(r_json["title"])            
        except Exception as e:        
            print("Request failed for ", movie_id)
            print(r.status_code)
            print(e)
        else:
            result.append(r_json)
    
    # get french tmdb title
    # add french language to PARAMS
    params["language"] = "fr-FR"
    
    result_french_title = []
    for i, movie_id in enumerate(new_movies):
        print(str((i+1)/new_movies_number * 100) + "%")
        try:
            print("Trying {}".format(movie_id))
            r = requests.get(url = config["tmdb"]["api_url"] + "/movie/" + movie_id, params = params)
            r_json = r.json()
            r_json = {"title": r_json["title"], "id": r_json["id"]}
            print(r_json["title"])            
        except Exception as e:        
            print("Request failed for ", movie_id)
            print(r.status_code)
            print(e)
        else:
            result_french_title.append(r_json)
    
    if len(result) > 0:
        result_df = pd.DataFrame(result)
        result_df["id"] = result_df["id"].astype(str)
        print("Number of movies retrieved by TMBD API", result_df.shape[0])
        
        result_df = result_df.rename(columns={"title": "english_title", "tagline": "english_tagline", "overview": "english_overview"})
    
        result_french_title_df = pd.DataFrame(result_french_title)
        print("Number of movies retrieved by TMBD API to get the french titles", result_french_title_df.shape[0])
        result_french_title_df["id"] = result_french_title_df["id"].astype(str)
        result_french_title_df = result_french_title_df.rename(columns={"title": "french_title"})
    
        new_tmdb_data = result_df.merge(result_french_title_df, on="id")
        new_tmdb_data = new_tmdb_data[['imdb_id'] + [col for col in new_tmdb_data if col != 'imdb_id']]
    
        now = datetime.now()
        dt_string = now.strftime("%d/%m/%Y")
        new_tmdb_data["intgration_date"] = dt_string
        
        print("Adding {} new movies in TMBD data file.".format(new_tmdb_data.shape[0]))
        data_tmdb_df = pd.concat([data_tmdb_df, new_tmdb_data], ignore_index=True)
        data_tmdb_df.to_pickle(tmdb_data_output_path)
    elif new_movies_number == 0:
        print("No new data to add")
    else:
        print("The TMBD API gave 0 result")
    
    return data_tmdb_df
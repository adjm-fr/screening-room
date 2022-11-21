import re, time, random
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

import pandas as pd
import dateparser

def get_showtimes(theaters_urls, selenium_bin_path, showtimes_output_path):

    chrome_path = selenium_bin_path + "/chromedriver.exe"
    driver = webdriver.Chrome(executable_path=chrome_path)
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)
    driver.get("https://www.allocine.fr")
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[onclick^='Didomi.setUserAgreeToAll']"))).click()
    time.sleep(random.randint(18, 20))

    output = []
    for theater in theaters:
        driver.get(theater)
        time.sleep(2)
        theater_title = driver.find_element_by_class_name("theater-cover-title").text
        print(theater_title)
        dates = driver.find_elements_by_class_name("calendar-date-link")
        not_disabled_dates = [ i for i, x in enumerate(dates) if "disabled" not in x.get_attribute("class") ]
        for i in not_disabled_dates:
            url_split = theater.split("/")
            url_split.insert(4, "d-" + str(i))
            new_url = "/".join(url_split)
            driver.get(new_url)
            time.sleep(3)
            date = [ x for x in driver.find_elements_by_class_name("calendar-date-link") if "current" in x.get_attribute("class") ]
            if len(date) > 1:
                raise "Several current date"
            else:
                day = date[0].text.replace("\n", " ")
                print(day)      
                movies = driver.find_elements_by_class_name("movie-card-theater")
                print(len(movies))
                for movie in movies:
                    showtimes_dict = {}
                    showtimes_dict["theater"] = theater_title
                    title = movie.find_element_by_class_name("meta-title-link").text
                    print(title)
                    try:
                        direction = movie.find_element_by_class_name("meta-body-direction").text
                    except:
                        directors = ""
                    else:
                        directors = re.search('(?<=De ).*', direction).group(0)
                        print(directors)                
                    try:
                        info = movie.find_element_by_class_name("meta-body-info").text
                        duration = re.search(r'(\d+h\s\d+min)', info).group(1)
                    except:
                        duration = ""
                    showtimes = movie.find_elements_by_class_name("showtimes-hour-item-value")
                    showtimes_list = "/".join([showtime.text.replace("\n", "") for showtime in showtimes])
                    print(showtimes_list)
                    showtimes_dict["day"] = day
                    showtimes_dict["movie"] = title
                    showtimes_dict["showtimes"] = showtimes_list
                    showtimes_dict["duration"] = duration
                    showtimes_dict["directors"] = directors
                    output.append(showtimes_dict)
                    
    print(len(output))

    driver.close()

    output_df = pd.DataFrame(output)
    output_df["day"] = output_df["day"].apply(lambda x: dateparser.parse(x).date())
    output_df["showtimes"] = output_df["showtimes"].apply(lambda x: x.split("/"))
    output_df = output_df.explode("showtimes")
    output_df["showtimes"] = output_df["day"].astype(str) + " " + output_df["showtimes"]
    output_df.drop(columns="day", inplace=True)
    output_df["showtimes"] = pd.to_datetime(output_df["showtimes"])
    output_df["is_weekend"] = output_df["showtimes"].dt.dayofweek > 4

    output_df.to_pickle(showtimes_output_path)

    return output_df
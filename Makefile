.PHONY: update run install

# Pull latest changes from all three repos
update:
	git -C ../movies_management pull
	git -C ../Allocine-Showtimes-Scraping pull
	git pull

# Start the Streamlit dashboard
run:
	streamlit run app.py

# Install all dependencies into the current venv
install:
	pip install -r requirements.txt
	pip install -r ../movies_management/requirements.txt
	pip install -r ../Allocine-Showtimes-Scraping/requirements.txt

.PHONY: update run install

# Pull latest changes from all three repos
update:
	git -C ../movies_management pull
	git -C ../Allocine-Showtimes-Scraping pull
	git pull

# Start the Streamlit dashboard
run:
	streamlit run app.py

# Install all dependencies using uv
install:
	uv sync
	cd ../movies_management && uv sync && cd ../cinema_dashboard
	cd ../Allocine-Showtimes-Scraping && uv sync && cd ../cinema_dashboard

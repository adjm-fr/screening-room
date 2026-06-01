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
# Sibling repos have their own .venv; unset VIRTUAL_ENV so uv targets each
# project's environment instead of warning about the activated cinema_dashboard one.
install:
	uv sync
	env -u VIRTUAL_ENV uv sync --project ../movies_management
	env -u VIRTUAL_ENV uv sync --project ../Allocine-Showtimes-Scraping

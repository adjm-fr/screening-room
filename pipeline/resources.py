from dagster import ConfigurableResource


class ScraperConfig(ConfigurableResource):
    """Paths and directories needed by the two scraper subprocesses."""

    allocine_dir: str
    movies_dir: str
    allocine_output_path: str
    movies_output_path: str

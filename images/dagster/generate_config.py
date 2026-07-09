import os
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


def detect_backend(connection_uri: str) -> str:
    if connection_uri.startswith("postgresql"):
        return "postgres"
    if connection_uri.startswith("mysql"):
        return "mysql"
    if connection_uri.startswith("sqlite"):
        return "sqlite"
    return "postgres"


def generate_dagster_yaml():
    backend = os.getenv("DB_BACKEND")
    if not backend:
        connection_uri = os.getenv("DAGSTER_DB_CONNECTION_URI", "")
        backend = detect_backend(connection_uri)

    script_dir = Path(__file__).parent
    env = Environment(loader=FileSystemLoader(str(script_dir)))
    template = env.get_template("dagster.yaml.j2")

    rendered = template.render(backend=backend)

    dagster_home = os.getenv("DAGSTER_HOME", "/opt/dagster/dagster_home")
    output_path = Path(dagster_home) / "dagster.yaml"
    output_path.write_text(rendered)

    print(f"dagster.yaml generated for backend: {backend}")


if __name__ == "__main__":
    generate_dagster_yaml()

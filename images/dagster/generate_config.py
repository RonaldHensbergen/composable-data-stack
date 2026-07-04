import os
from jinja2 import Environment, FileSystemLoader

def generate_dagster_yaml():
    backend = os.getenv("DB_BACKEND", "postgres")

    env = Environment(loader=FileSystemLoader("."))
    template = env.get_template("dagster.yaml.j2")

    rendered = template.render(backend=backend)

    with open("dagster.yaml", "w") as f:
        f.write(rendered)

    print(f"✅ dagster.yaml generated for backend: {backend}")

if __name__ == "__main__":
    generate_dagster_yaml()

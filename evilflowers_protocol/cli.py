import argparse
from pathlib import Path

from pydantic2md import pydantic2md

from evilflowers_protocol import schema


def generate_docs(output):
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    models = [
        schema.__dict__[k]
        for k in dir(schema)
        if isinstance(schema.__dict__.get(k), type) and issubclass(schema.__dict__[k], schema.BaseModel)
    ]

    with output_path.open("w") as f:
        for model in models:
            f.write(pydantic2md(model))
            f.write("\n")
    print(f"Documentation generated at {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Evilflowers Protocol CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_docs_parser = subparsers.add_parser("generate_docs", help="Generate API docs from Pydantic models")
    generate_docs_parser.add_argument("--output", default="docs/api.md", help="Output file for the generated documentation")

    args = parser.parse_args()

    if args.command == "generate_docs":
        generate_docs(args.output)

if __name__ == "__main__":
    main()

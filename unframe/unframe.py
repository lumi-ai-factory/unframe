#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import subprocess
import sys
import time
import yaml

from itertools import chain
from jinja2 import Environment
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("./out")
DEFAULT_SCRIPTS_DIR = DEFAULT_OUTPUT_DIR / "scripts"
DEFAULT_STDIO_DIR = DEFAULT_OUTPUT_DIR / "stdio"
DEFAULT_RESULTS_DIR = DEFAULT_OUTPUT_DIR / "results"


def add_parser_gen(subparsers: argparse.Action) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("generate", help="Generate test scripts")

    parser.add_argument("defs_dir", type=Path, help="Path to YAML test definitions dir")
    parser.add_argument(
        "scripts_dir", default=DEFAULT_SCRIPTS_DIR, type=Path, help="Path to test scripts dir",
    )

    return parser


def add_parser_exe(subparsers: argparse.Action) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("execute", help="Execute test scripts")

    parser.add_argument(
        "scripts_dir", default=DEFAULT_SCRIPTS_DIR, type=Path, help="Path to test scripts dir",
    )
    parser.add_argument(
        "stdio_dir", default=DEFAULT_STDIO_DIR, type=Path, help="Path to test stdio dir",
    )

    return parser


def add_parser_val(subparsers: argparse.Action) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("validate", help="Validate test results")

    parser.add_argument("defs_dir", type=Path, help="Path to YAML test definitions dir")
    parser.add_argument(
        "stdio_dir", default=DEFAULT_STDIO_DIR, type=Path, help="Path to test stdio dir",
    )
    parser.add_argument(
        "results_dir", default=DEFAULT_RESULTS_DIR, type=Path, help="Path to test results dir",
    )

    return parser


def add_parser_run(subparsers: argparse.Action) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("run", help="Generate, execute and validate tests")

    parser.add_argument("defs_dir", type=Path, help="Path to YAML test definitions dir")
    parser.add_argument(
        "scripts_dir", default=DEFAULT_SCRIPTS_DIR, type=Path, help="Path to test scripts dir",
    )
    parser.add_argument(
        "stdio_dir", default=DEFAULT_STDIO_DIR, type=Path, help="Path to test stdio dir",
    )
    parser.add_argument(
        "results_dir", default=DEFAULT_RESULTS_DIR, type=Path, help="Path to test results dir",
    )

    return parser


def load_defs(path: Path) -> list:
    definitions = []

    if path.is_dir():
        yaml_files = chain(path.glob("*.yaml"), path.glob("*.yml"))
        definitions += [yaml.safe_load(f.read_text()) for f in yaml_files]
    else:
        raise ValueError("Provided path must point to an existing directory.")

    return definitions


def get_shared_params(param_dict: dict, param_file: Path) -> dict:
    params = {}

    if param_dict:
        params.update(param_dict)

    if param_file:
        with param_file.open() as fp:
            params.update(json.load(fp))

    return params


def aggregate_params(test_params: dict, shared_params: dict) -> dict:
    params = {}

    for d in shared_params, test_params:  # Test params take precedence
        for k, v in d.items():
            if isinstance(v, list):
                params[k] = " ".join([f'"{x}"' for x in v])
            else:
                params[k] = v

    return params


def generate(args: argparse.Namespace) -> list:
    scripts = []

    # Load test definitions
    definitions = load_defs(path=args.defs_dir)
    if not definitions:
        sys.exit("No test definitions provided.")

    # Define parameters shared by all tests
    shared_params = get_shared_params(param_dict=args.params, param_file=args.param_file)

    # Ensure output directory exists
    scripts_dir = args.scripts_dir
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # Initialize template environment
    env = Environment()

    for dfn in definitions:
        template = env.from_string(dfn["job"])
        params = aggregate_params(test_params=dfn["params"], shared_params=shared_params)
        job_string = template.render(params=params, command=dfn["command"])

        script_file = scripts_dir / (dfn["name"] + ".sh")
        with script_file.open(mode="w") as f:
            f.write(job_string)
        scripts.append(str(script_file))

    return scripts


def execute(args: argparse.Namespace) -> list:
    stdio_files = []
    job_ids = []

    # Create output directory
    timestamp = time.strftime("%Y-%m-%dT%H%M%S")
    stdio_dir = args.stdio_dir / timestamp
    stdio_dir.mkdir(parents=True, exist_ok=True)

    scripts_dir = args.scripts_dir

    for script in scripts_dir.glob("*.sh"):
        stdio_path = stdio_dir / (script.stem + ".out")

        proc = subprocess.run(
            ["sbatch", "-o", stdio_path, "--parsable", script],
            capture_output=True,
            universal_newlines=True,
        )

        stdio_files.append(str(stdio_path))
        job_ids.append(proc.stdout.strip())

    return timestamp, stdio_files, job_ids


def validate(args: argparse.Namespace):
    raise NotImplementedError


def run(args: argparse.Namespace):
    raise NotImplementedError


def main():
    parser = argparse.ArgumentParser(prog="unframe", description="Tiny YAML-driven test runner")
    subparsers = parser.add_subparsers(required=True)

    # Parser for `generate` subcommand
    parser_gen = add_parser_gen(subparsers)
    parser_gen.set_defaults(func=generate)

    # Parser for `execute` subcommand
    parser_exe = add_parser_exe(subparsers)
    parser_exe.set_defaults(func=execute)

    # Parser for `validate` subcommand
    parser_val = add_parser_val(subparsers)
    parser_val.set_defaults(func=validate)

    # Parser for `run` subcommand
    parser_run = add_parser_run(subparsers)
    parser_run.set_defaults(func=run)

    # Shared parameters for tests
    parser.add_argument(
        "-p", "--params", type=dict, help="JSON string with shared parameters dict",
    )
    parser.add_argument(
        "--param-file", "--pf", type=Path, help="Path to JSON file with shared parameters dict",
    )

    args = parser.parse_args()

    # Execute operation defined by subcommand, print output
    results = args.func(args)
    print(results)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
import yaml

from itertools import chain
from jinja2 import Environment
from pathlib import Path


def load_defs(path: Path) -> list:
    definitions = []

    if path.is_dir():
        yaml_files = chain(path.glob("*.yaml"), path.glob("*.yml"))
        definitions += [yaml.safe_load(f.read_text()) for f in yaml_files]
    else:
        raise ValueError("Provided path must point to an existing directory.")

    return definitions


def get_shared_params(params_dict: dict, params_file: Path) -> dict:
    params = {}

    if params_dict:
        params.update(params_dict)

    if params_file:
        with params_file.open() as fp:
            params.update(json.load(fp))

    return params


def preprocess_params(test_params: dict, shared_params: dict) -> dict:
    params = {}

    for d in shared_params, test_params:  # Test params take precedence
        for k, v in d.items():
            if isinstance(v, list):
                params[k] = " ".join([f'"{x}"' for x in v])
            else:
                params[k] = v

    return params


def generate(definitions: list, shared_params: dict, args: argparse.Namespace) -> list:
    scripts = []

    scripts_dir = args.scripts_dir
    scripts_dir.mkdir(parents=True, exist_ok=True)

    env = Environment()

    for dfn in definitions:
        template = env.from_string(dfn["job"])
        params = preprocess_params(test_params=dfn["params"], shared_params=shared_params)
        job_string = template.render(params=params, command=dfn["command"])

        script_file = scripts_dir / (dfn["name"] + ".sh")
        with script_file.open(mode="w") as f:
            f.write(job_string)
        scripts.append(str(script_file))

    return scripts


def run(definitions: list, shared_params: dict, args: argparse.Namespace):
    raise NotImplementedError


def validate(definitions: list, shared_params: dict, args: argparse.Namespace):
    raise NotImplementedError


def main():
    parser = argparse.ArgumentParser(prog="unframe", description="Tiny YAML-driven test runner")
    parser.add_argument(
        "-p", "--params", type=dict,
        help="JSON string containing a dictionary of shared parameters",
    )
    parser.add_argument(
        "--params-file", "--pf", type=Path,
        help="Path to JSON file containing a dictionary of shared parameters",
    )
    subparsers = parser.add_subparsers(required=True)

    # Parser for `generate` subcommand
    parser_gen = subparsers.add_parser("generate", help="Generate test scripts")
    parser_gen.add_argument("defs_dir", type=Path, help="Path to YAML test definitions input dir")
    parser_gen.add_argument("scripts_dir", type=Path, help="Path to test scripts output dir")
    parser_gen.set_defaults(func=generate)

    # Parser for `run` subcommand
    parser_run = subparsers.add_parser("run", help="Run test scripts")
    parser_run.add_argument("defs_dir", type=Path, help="Path to YAML test definitions input dir")
    parser_run.add_argument("scripts_dir", type=Path, help="Path to test scripts input dir")
    parser_run.add_argument("scores_dir", type=Path, help="Path to test scores output dir")
    parser_run.set_defaults(func=run)

    # Parser for `validate` subcommand
    parser_val = subparsers.add_parser("validate", help="Validate test results")
    parser_val.add_argument("defs_dir", type=Path, help="Path to YAML test definitions input dir")
    parser_val.add_argument("scores_dir", type=Path, help="Path to test scores input dir")
    parser_val.add_argument("results_dir", type=Path, help="Path to test results output dir")
    parser_val.set_defaults(func=validate)

    args = parser.parse_args()

    # Load test definitions
    definitions = load_defs(path=args.defs_dir)
    if not definitions:
        sys.exit("No YAML files provided.")

    # Define parameters shared by all tests
    shared_params = get_shared_params(params_dict=args.params, params_file=args.params_file)

    # Execute operation defined by subcommand
    result = args.func(definitions=definitions, shared_params=shared_params, args=args)
    print(result)


if __name__ == "__main__":
    main()

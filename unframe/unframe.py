#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
import yaml

from itertools import chain
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


def generate(definitions: list, shared_params: dict, args: argparse.Namespace):
    raise NotImplementedError


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
    args.func(definitions=definitions, shared_params=shared_params, args=args)


if __name__ == "__main__":
    main()

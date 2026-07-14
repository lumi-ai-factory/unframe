#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import subprocess
import sys
import time
import yaml

from itertools import chain
from jinja2 import Environment
from pathlib import Path

DEFAULT_JOB_DIR = "jobs"
DEFAULT_OUT_DIR = "output"
DEFAULT_RES_DIR = "results"


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


def format_params_bash(test_params: dict, shared_params: dict) -> dict:
    params = {}

    for d in shared_params, test_params:  # Test params take precedence
        for k, v in d.items():
            if isinstance(v, list):
                params[k] = " ".join([f'"{x}"' for x in v])
            else:
                params[k] = v

    return params


def get_function_from_string(function_string):
    namespace = {}
    exec(function_string, namespace)
    return list(namespace.values())[-1]


def log_results(results, res_path: Path, timestamp: str, test_name: str = ""):
    fieldnames = ["time"]
    base_row = {"time": timestamp}

    if test_name:
        fieldnames += ["name"]
        base_row["name"] = test_name

    fieldnames += ["status"]

    if isinstance(results, tuple):
        fieldnames += [key for key in results[1][0].keys() if key not in fieldnames]

    write_header = False if res_path.exists() else True

    with open(res_path, "a") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        if write_header:
            writer.writeheader()

        if isinstance(results, bool):
            row = base_row.copy()
            row["status"] = 0 if results else 1
            writer.writerow(row)
        elif isinstance(results, tuple):
            for r in results[1]:
                row = base_row.copy()
                row.update(r)
                writer.writerow(row)
        else:
            raise TypeError(f"Unknown result type: {type(results)}")


def generate(def_dir: Path, job_dir: Path, params: dict = None, param_file: Path = None) -> list:
    jobs = []

    definitions = load_defs(path=def_dir)
    if not definitions:
        sys.exit("No test definitions provided.")

    shared_params = get_shared_params(param_dict=params, param_file=param_file)

    job_dir = job_dir
    job_dir.mkdir(parents=True, exist_ok=True)

    # Initialize Jinja2 template environment
    env = Environment()

    for dfn in definitions:
        template = env.from_string(dfn["job"])
        params = format_params_bash(test_params=dfn.get("params"), shared_params=shared_params)
        job_string = template.render(params=params, command=dfn["command"])

        script_file = job_dir / (dfn["name"] + ".sh")
        with script_file.open(mode="w") as f:
            f.write(job_string)
        jobs.append(str(script_file))

    return jobs


def execute(job_dir: Path, out_dir: Path) -> list:
    out_files = []
    job_ids = []

    timestamp = time.strftime("%Y-%m-%dT%H%M%S")

    job_dir = job_dir
    out_dir = out_dir / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    for script in job_dir.glob("*.sh"):
        out_path = out_dir / (script.stem + ".out")

        proc = subprocess.run(
            ["sbatch", "-o", out_path, "--parsable", script],
            capture_output=True,
            universal_newlines=True,
        )

        out_files.append(str(out_path))
        job_ids.append(proc.stdout.strip())

    return timestamp, out_files, job_ids


def validate(
    def_dir: Path, out_dir: Path, res_dir: Path, params: dict = None, param_file: Path = None,
) -> Path:
    res_files = []

    definitions = load_defs(path=def_dir)
    if not definitions:
        sys.exit("No test definitions provided.")

    shared_params = get_shared_params(param_dict=params, param_file=param_file)

    out_dir = out_dir
    res_dir = res_dir / out_dir.parts[-1]
    res_dir.mkdir(parents=True, exist_ok=True)

    summary_path = res_dir / "summary.csv"

    for dfn in definitions:
        test_name = dfn["name"]
        params = dfn.get("params")

        out_path = out_dir / (test_name + ".out")
        res_path = res_dir / (test_name + ".csv")

        parse_fn_str = dfn.get("parse")
        validate_fn_str = dfn.get("validate")

        parse_fn = get_function_from_string(parse_fn_str) if parse_fn_str else None
        validate_fn = get_function_from_string(validate_fn_str) if validate_fn_str else None

        if not out_path.is_file():
            raise ValueError(f"File {str(out_path)} not found.")
        text = out_path.read_text()

        results = None
        if parse_fn:
            results = parse_fn(text, params)
        if validate_fn:
            results = validate_fn(results, params)

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        log_results(results, res_path, timestamp)
        log_results(results[0], summary_path, timestamp, test_name)

    return summary_path


def run(
    def_dir: Path, job_dir: Path, out_dir: Path, res_dir: Path, params: dict = None,
    param_file: Path = None,
):
    raise NotImplementedError


def main():
    parser = argparse.ArgumentParser(prog="unframe", description="Tiny YAML-driven test runner")

    parser.add_argument("def_dir", type=Path, help="Path to YAML test definitions dir")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-g", "--gen", action="store_true", help="Generate test jobs")
    group.add_argument("-x", "--exe", action="store_true", help="Execute test jobs")
    group.add_argument("-l", "--val", action="store_true", help="Validate test jobs")
    group.add_argument(
        "-r", "--run", action="store_true", help="Generate, execute and validate test jobs",
    )

    parser.add_argument(
        "-j", "--job-dir", default=DEFAULT_JOB_DIR, type=Path, help="Path to test jobs dir",
    )
    parser.add_argument(
        "-o", "--out-dir", default=DEFAULT_OUT_DIR, type=Path, help="Path to test output dir",
    )
    parser.add_argument(
        "-s", "--res-dir", default=DEFAULT_RES_DIR, type=Path, help="Path to test results dir",
    )

    parser.add_argument(
        "-p", "--params", type=dict, help="JSON string with shared parameters dict",
    )
    parser.add_argument(
        "--param-file", "--pf", type=Path, help="Path to JSON file with shared parameters dict",
    )

    args = parser.parse_args()

    if args.gen:
        results = generate(args.def_dir, args.job_dir, args.params, args.param_file)
    elif args.exe:
        results = execute(args.job_dir, args.out_dir)
    elif args.val:
        results = validate(args.def_dir, args.out_dir, args.res_dir, args.params, args.param_file)
    elif args.run:
        results = run(
            args.def_dir, args.job_dir, args.out_dir, args.res_dir, args.params, args.param_file,
        )
    else:
        sys.exit("No operation specified.")

    print(results)


if __name__ == "__main__":
    main()

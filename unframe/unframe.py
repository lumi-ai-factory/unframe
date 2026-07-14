#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import shlex
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


def load_defs(path: Path, names: list = [], tags: list = []) -> list:
    definitions = []

    if not path.is_dir():
        raise ValueError("Provided path must point to an existing directory.")

    yaml_files = chain(path.glob("*.yaml"), path.glob("*.yml"))
    for f in yaml_files:
        dfn = yaml.safe_load(f.read_text())

        if names and dfn.get("name") not in names:
            continue

        if tags:
            dfn_tags = dfn.get("tags") or []
            if not set(tags) & set(dfn_tags):
                continue

        definitions.append(dfn)

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


def get_param_cli_args(param_dict: dict, param_file: Path) -> list:
    param_args = []

    if param_dict:
        param_args += ["-p", json.dumps(param_dict)]
    if param_file:
        param_args += ["--pf", str(param_file)]

    return param_args


def generate(
    def_dir: Path, job_dir: Path, params: dict = {}, param_file: Path = None,
    names: list = [], tags: list = [],
) -> list:
    jobs = []

    definitions = load_defs(def_dir, names, tags)
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

        job_file = job_dir / (dfn["name"] + ".sh")
        with job_file.open(mode="w") as f:
            f.write(job_string)
        jobs.append(str(job_file))

    return jobs


def execute(
    def_dir: Path, job_dir: Path, out_dir: Path, names: list = [], tags: list = [],
) -> list:
    out_files = []
    job_ids = []

    definitions = load_defs(def_dir, names, tags)
    if not definitions:
        sys.exit("No test definitions provided.")

    timestamp = time.strftime("%Y-%m-%dT%H%M%S")

    job_dir = job_dir
    out_dir = out_dir / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    for dfn in definitions:
        job_path = job_dir / (dfn["name"] + ".sh")
        out_path = out_dir / (dfn["name"] + ".out")

        if not job_path.is_file():
            print(f"Test '{test_name}': file '{str(job_path)}' not found.")
            continue

        proc = subprocess.run(
            ["sbatch", "-o", out_path, "--parsable", job_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        out_files.append(str(out_path))
        job_ids.append(proc.stdout.strip())

    return timestamp, out_files, job_ids


def validate(
    def_dir: Path, out_dir: Path, res_dir: Path, params: dict = {}, param_file: Path = None,
    names: list = [], tags: list = [],
) -> Path:
    res_files = []

    definitions = load_defs(def_dir, names, tags)
    if not definitions:
        sys.exit("No test definitions provided.")

    shared_params = get_shared_params(param_dict=params, param_file=param_file)

    out_dir = out_dir
    res_dir = res_dir
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
            print(f"Test '{test_name}': file '{str(out_path)}' not found.")
            continue

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
    def_dir: Path, job_dir: Path, out_dir: Path, res_dir: Path, params: dict = {},
    param_file: Path = None, names: list = [], tags: list = [],
) -> str:
    # Get Slurm account from shared params for submitting aggregation job
    shared_params = get_shared_params(param_dict=params, param_file=param_file)
    account = shared_params.get("account")
    if not account:
        sys.exit("Must provide Slurm account (`account`) as a shared parameter.")

    jobs = generate(def_dir, job_dir, params, param_file, names, tags)
    timestamp, out_files, job_ids = execute(def_dir, job_dir, out_dir, names, tags)

    out_dir = out_dir / timestamp
    res_dir = res_dir / out_dir.parts[-1]

    param_args = get_param_cli_args(param_dict=params, param_file=param_file)

    val_submit_args = [
        "sbatch", "-A", account, "-d", ",".join(job_ids),
        "-J", "unframe", "-p", "debug", "--parsable",
    ]

    val_script_str = (
        "#!/bin/bash\n"
        f"{sys.argv[0]} -l -o {out_dir} -s {res_dir} "
        f"{' '.join(shlex.quote(str(arg)) for arg in param_args)} "
        f"{' '.join('-n ' + n for n in names)} "
        f"{' '.join('-t ' + t for t in tags)} "
        f"{def_dir}\n"
    )

    proc = subprocess.Popen(
        val_submit_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    outs, errs = proc.communicate(val_script_str)

    val_job_id = outs.strip()

    return val_job_id


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

    parser.add_argument(
        "-n", "--name", type=str, default=[], action="append",
        help="run tests matching this name (repeatable)",
    )
    parser.add_argument(
        "-t", "--tag", type=str, default=[], action="append",
        help="run tests matching this tag (repeatable)",
    )

    args = parser.parse_args()

    if args.gen:
        results = generate(
            args.def_dir, args.job_dir, args.params, args.param_file, args.name, args.tag,
        )
    elif args.exe:
        results = execute(args.def_dir, args.job_dir, args.out_dir, args.name, args.tag)
    elif args.val:
        results = validate(
            args.def_dir, args.out_dir, args.res_dir, args.params,
            args.param_file, args.name, args.tag,
        )
    elif args.run:
        results = run(
            args.def_dir, args.job_dir, args.out_dir, args.res_dir,
            args.params, args.param_file, args.name, args.tag,
        )
    else:
        sys.exit("No operation specified.")

    print(results)


if __name__ == "__main__":
    main()

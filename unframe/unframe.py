#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unframe — Simple YAML-driven test runner 

YAML should hage:
  - name, tags (optional)
  - params: dict of arrays (Cartesian)
  - env: dict (optional)
  - job: list of argv items; supports {{ var }} and {{ extra_args.foo }}
    * use "{{ snippet.NAME }}" to inject multi-line snippet as one argv argument (e.g., to pass to 'bash -lc')
  - snippets: [{name, content}] (optional)
  - parse: |  def parse(text, params): return <anything>
  - validate: |  def validate(results, params): return bool or (bool, "message")

Runner:
- Renders job with params + extra_args
- Runs command
- Calls parse(text, params) -> results (any object)
- Calls validate(results, params) -> pass/fail (+ message)
- Logs per permutation to CSV (results JSON-serialized if possible)
"""

import argparse
import csv
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import time
import yaml

from pathlib import Path


VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_\.]+)\s*\}\}")
SNIPPET_TOKEN_RE = re.compile(r"^\{\{\s*snippet\.([A-Za-z0-9_-]+)\s*\}\}$")


def ctx_lookup(ctx, path):
    parts = path.split(".")
    cur = ctx
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return ""
    return cur


def render_string(s, ctx):
    # Render {{ var }} or {{ foo.bar }}
    def repl(m):
        key = m.group(1).strip()
        val = ctx_lookup(ctx, key)
        return "" if val is None else str(val)
    return VAR_RE.sub(repl, s)


def render_job_to_argv(spec, ctx):
    """
    job: list of strings
      - if item is exactly "{{ snippet.NAME }}", inject snippet content as ONE arg (after rendering)
      - else render with ctx and shlex.split (so "--flag 8" can be written together)
    snippets: [{name, content}]
    """
    job = spec.get("job", [])
    if not isinstance(job, list) or not job:
        raise ValueError("`job` must be a non-empty list")

    sn_map = {}
    for s in (spec.get("snippets") or []):
        sn_map[s["name"]] = s["content"]

    argv = []
    for item in job:
        raw = str(item).strip()

        # detect snippet BEFORE generic rendering
        m = SNIPPET_TOKEN_RE.match(raw)
        if m:
            sn_name = m.group(1)
            if sn_name not in sn_map:
                raise ValueError("snippet '{}' not found".format(sn_name))
            body = render_string(sn_map[sn_name], ctx)
            argv.append(body)
            continue

        rendered = render_string(raw, ctx)
        argv.extend(shlex.split(rendered))
    return argv


def cartesian_params(param_dict):
    if not param_dict:
        return [({})]
    keys = list(param_dict.keys())
    vals = [param_dict[k] for k in keys]
    combos = []
    for tup in itertools.product(*vals):
        combos.append(dict(zip(keys, tup)))
    return combos


def run_argv(argv, env, cwd, timeout, verbose=False):
    env_all = os.environ.copy()
    env_all.update({k: str(v) for k, v in env.items()})
    t0 = time.time()

    if verbose:
        print("argv:", " ".join(argv))

    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env_all,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if isinstance(proc.stdout, bytes):
        proc.stdout = proc.stdout.decode("utf-8", "replace")
    if isinstance(proc.stderr, bytes):
        proc.stderr = proc.stderr.decode("utf-8", "replace")

    if verbose:
        print("proc.stdout:", proc.stdout)
        print("proc.stderr:", proc.stderr)

    dt = time.time() - t0
    return proc.returncode, proc.stdout, proc.stderr, dt


def write_perflog(prefix, sysenv, testname, params, duration_s, status, message, results):
    outdir = Path(prefix) / "perflogs" / sysenv
    outdir.mkdir(parents=True, exist_ok=True)
    # try to JSON-serialize results; fallback to repr
    try:
        results_json = json.dumps(results, separators=(",", ":"), sort_keys=True)
    except Exception:
        results_json = json.dumps({"_repr": repr(results)})
    row = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "name": testname,
        "params": json.dumps(params, separators=(",", ":"), sort_keys=True),
        "sysenv": sysenv,
        "duration_s": "{:.3f}".format(duration_s),
        "status": "PASS" if status else "FAIL",
        "message": message or "",
        "results": results_json,
    }
    fpath = outdir / (testname + ".csv")
    new = not fpath.exists()
    with fpath.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


def load_func(code_str, func_name):
    """
    Exec code_str and return function named func_name.
    Trusted code: full Python environment.
    """
    ns = {}
    exec(code_str, ns, ns)
    fn = ns.get(func_name)
    if not callable(fn):
        raise ValueError("`{}` block must define a function named `{}`".format(func_name, func_name))
    return fn


def require_parse(spec):
    code = spec.get("parse")
    if not code:
        return None
    return load_func(code, "parse")


def require_validate(spec):
    code = spec.get("validate")
    if not code:
        return None
    return load_func(code, "validate")


def load_tests(dirpath, name_filter, tag_filter):
    tests = []
    for p in sorted(Path(dirpath).glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text())
        except Exception as e:
            print("[WARN] Failed to parse {}: {}".format(p, e), file=sys.stderr)
            continue
        data["_file"] = str(p)
        data.setdefault("tags", [])
        data.setdefault("params", {})
        data.setdefault("env", {})
        if name_filter and data.get("name") not in set(name_filter):
            continue
        if tag_filter and not (set(tag_filter) & set(data.get("tags", []))):
            continue
        tests.append(data)
    return tests


def main():
    ap = argparse.ArgumentParser(prog="unframe", description="Tiny YAML-driven test runner")
    ap.add_argument("-d", "--dir", required=True, help="tests directory (YAML files)")
    ap.add_argument("-n", "--name", action="append", help="run tests matching this name (repeatable)")
    ap.add_argument("-t", "--tag", action="append", help="run tests matching this tag (repeatable)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--sysenv", default="generic:default", help="label in perflogs")
    ap.add_argument("--timeout", type=int, default=None, help="per-run timeout (seconds)")
    ap.add_argument("--prefix", default="out", help="output prefix (perflogs/...)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--extra-args", default="{}", help='JSON dict for templating (e.g. \'{"account":"proj","partition":"dev-g"}\')')
    args = ap.parse_args()

    try:
        extra_args = json.loads(args.extra_args)
        if not isinstance(extra_args, dict):
            raise ValueError("must be a JSON object")
    except Exception as e:
        print("Invalid --extra-args JSON: {}".format(e), file=sys.stderr)
        sys.exit(2)

    tests = load_tests(args.dir, args.name or [], args.tag or [])
    if not tests:
        print("No tests selected.", file=sys.stderr)
        sys.exit(2)

    any_fail = False

    for spec in tests:
        name = spec.get("name") or Path(spec["_file"]).stem
        desc = spec.get("description", "")
        param_space = cartesian_params(spec.get("params", {}))
        pkeys = list(spec.get("params", {}).keys())

        print("\n=== {} ({} permutations) ===".format(name, len(param_space)))
        if desc:
            print(desc)
        if pkeys:
            hdr = " ".join("{:>14s}".format(h) for h in (pkeys + ["status", "message"]))
            print(hdr)

        # Load parse & validate once per test file
        parse_fn = require_parse(spec)
        validate_fn = require_validate(spec)

        for params in param_space:
            # env = static + params
            env_vars = dict(spec.get("env", {}))
            env_vars.update({k: v for k, v in params.items()})

            # templating context
            ctx = {"extra_args": extra_args}
            ctx.update(params)

            # argv
            try:
                argv = render_job_to_argv(spec, ctx)
            except Exception as e:
                line = " ".join("{:>14s}".format(str(params.get(k, ""))) for k in pkeys) if pkeys else ""
                print((line + " ").rstrip(), "[FAIL]", "spec error: {}".format(e))
                write_perflog(args.prefix, args.sysenv, name, params, 0.0, False, "spec error: {}".format(e), {})
                any_fail = True
                continue

            if args.dry_run:
                compact = " ".join(shlex.quote(a) for a in argv)
                if pkeys:
                    line = " ".join("{:>14s}".format(str(params.get(k, ""))) for k in pkeys)
                    print(line, "{:>14s}".format("DRYRUN"), compact)
                else:
                    print("DRYRUN", compact)
                # no perflog for dry-run
                continue

            # run
            try:
                rc, out, err, dt = run_argv(
                    argv, env_vars, spec.get("workdir"), args.timeout, verbose=args.verbose,
                )
            except subprocess.TimeoutExpired:
                line = " ".join("{:>14s}".format(str(params.get(k, ""))) for k in pkeys) if pkeys else ""
                print((line + " ").rstrip(), "[FAIL]", "timeout")
                write_perflog(args.prefix, args.sysenv, name, params, float(args.timeout or 0), False, "timeout", {"rc": "timeout"})
                any_fail = True
                continue
            except FileNotFoundError as e:
                line = " ".join("{:>14s}".format(str(params.get(k, ""))) for k in pkeys) if pkeys else ""
                print((line + " ").rstrip(), "[FAIL]", "exec error: {}".format(e))
                write_perflog(args.prefix, args.sysenv, name, params, 0.0, False, "exec error", {"error": str(e)})
                any_fail = True
                continue

            # parse (optional; if missing, pass stdout through)
            try:
                if parse_fn:
                    results = parse_fn(out, dict(params))
                else:
                    results = {"stdout": out}
            except Exception as e:
                msg = "parse error: {}".format(e)
                line = " ".join("{:>14s}".format(str(params.get(k, ""))) for k in pkeys) if pkeys else ""
                print((line + " ").rstrip(), "[FAIL]", msg)
                write_perflog(args.prefix, args.sysenv, name, params, dt, False, msg, {"rc": rc})
                any_fail = True
                continue

            # validate (optional; default to rc==0)
            passed = (rc == 0)
            vmsg = ""
            try:
                if validate_fn:
                    vres = validate_fn(results, dict(params))
                    if isinstance(vres, tuple) and len(vres) >= 1:
                        passed = bool(vres[0])
                        vmsg = "" if len(vres) == 1 else str(vres[1])
                    else:
                        passed = bool(vres)
                else:
                    # default: just returncode
                    passed = (rc == 0)
            except Exception as e:
                passed = False
                vmsg = "validate error: {}".format(e)

            # print one line
            if pkeys:
                line = " ".join("{:>14s}".format(str(params.get(k, ""))) for k in pkeys)
                print(line, "[{}]".format("PASS" if passed else "FAIL"), vmsg)
            else:
                print("[{}]".format("PASS" if passed else "FAIL"), vmsg)

            write_perflog(args.prefix, args.sysenv, name, params, dt, passed, vmsg, results)

            if not passed:
                any_fail = True

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()


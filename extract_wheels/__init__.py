"""extract_wheels

extract_wheels resolves and fetches artifacts transitively from the Python Package Index (PyPI) based on a
requirements.txt. It generates the required BUILD files to consume these packages as Python libraries.

Under the hood, it depends on the `pip wheel` command to do resolution, download, and compilation into wheels.
"""
import argparse
import glob
import os
import subprocess
import sys

from extract_wheels.lib import bazel, requirements


def configure_reproducible_wheels() -> None:
    """Modifies the environment to make wheel building reproducible.

    Wheels created from sdists are not reproducible by default. We can however workaround this by
    patching in some configuration with environment variables.
    """

    # wheel, by default, enables debug symbols in GCC. This incidentally captures the build path in the .so file
    # We can override this behavior by disabling debug symbols entirely.
    # https://github.com/pypa/pip/issues/6505
    if "CFLAGS" in os.environ:
        os.environ["CFLAGS"] += " -g0"
    else:
        os.environ["CFLAGS"] = "-g0"

    # set SOURCE_DATE_EPOCH to 1980 so that we can use python wheels
    # https://github.com/NixOS/nixpkgs/blob/master/doc/languages-frameworks/python.section.md#python-setuppy-bdist_wheel-cannot-create-whl
    if "SOURCE_DATE_EPOCH" not in os.environ:
        os.environ["SOURCE_DATE_EPOCH"] = "315532800"

    # Python wheel metadata files can be unstable.
    # See https://bitbucket.org/pypa/wheel/pull-requests/74/make-the-output-of-metadata-files/diff
    if "PYTHONHASHSEED" not in os.environ:
        os.environ["PYTHONHASHSEED"] = "0"


def _dl_wheel(req, additional_wheel_opts):
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
    ] + additional_wheel_opts + ["{}{}".format(req.name, req.specifier)]
    return subprocess.run(cmd)


def _fetch_packages_parallel(requirements_filepath):
    from pip._internal.req.req_file import parse_requirements
    from pip._internal.download import PipSession
    from concurrent.futures import ThreadPoolExecutor

    # Extract 'pip wheel' options (https://pip.pypa.io/en/stable/reference/pip_wheel/#options)
    # from top of requirements.txt file
    with open(requirements_filepath, "r") as f:
        requirements_txt_lines = f.readlines()
    wheel_cmd_opts = []
    for l in requirements_txt_lines:
        if l.startswith("--"):
            wheel_cmd_opts.extend(l.split())

    parsed_reqs = list(parse_requirements(requirements_filepath, session=PipSession()))

    with ThreadPoolExecutor(5) as ex:
        completed_procs = ex.map(
            _dl_wheel,
            [r.req for r in parsed_reqs],
            [wheel_cmd_opts for _ in range(len(parsed_reqs))],
        )
    failed_procs = [proc for proc in completed_procs if proc.returncode != 0]
    if failed_procs:
        # TODO(Jonathon): Obviously don't handle the error in such a poor way.
        raise RuntimeError(failed_procs)



def main() -> None:
    """Main program.

    Exits zero on successful program termination, non-zero otherwise.
    """

    configure_reproducible_wheels()

    parser = argparse.ArgumentParser(
        description="Resolve and fetch artifacts transitively from PyPI"
    )
    parser.add_argument(
        "--requirements",
        action="store",
        required=True,
        help="Path to requirements.txt from where to install dependencies",
    )
    parser.add_argument(
        "--repo",
        action="store",
        required=True,
        help="The external repo name to install dependencies. In the format '@{REPO_NAME}'",
    )
    parser.add_argument(
        "--precompiled",
        action="store_true",
        help="If set, assumes requirements.txt is a full transitive tree of locked dependencies. Enables parallel package fetching.",
    )
    args = parser.parse_args()

    wheel_cmd = [sys.executable, "-m", "pip", "wheel", "-r", args.requirements]
    if args.precompiled:
        _fetch_packages_parallel(
            requirements_filepath=args.requirements
        )
    else:
        # Assumes any errors are logged by pip so do nothing. This command will fail if pip fails
        subprocess.check_output(wheel_cmd)

    extras = requirements.parse_extras(args.requirements)

    targets = [
        '"%s%s"' % (args.repo, bazel.extract_wheel(whl, extras))
        for whl in glob.glob("*.whl")
    ]

    with open("requirements.bzl", "w") as requirement_file:
        requirement_file.write(
            bazel.generate_requirements_file_contents(args.repo, targets)
        )

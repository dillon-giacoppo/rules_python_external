import pkg_resources
import os
import setuptools

from typing import List

from src import wheel


def install_entry_points(extracted_whl_directory) -> List[str]:
    try:
        distribution = next(pkg_resources.find_distributions(extracted_whl_directory))
    except StopIteration:
        raise RuntimeError(f"Could not find in Python distribution in directory: {extracted_whl_directory}")

    entry_points = distribution.get_entry_map("console_scripts").items()
    bin_dir_path = os.path.join(extracted_whl_directory, "bin")
    os.makedirs(bin_dir_path, exist_ok=True)

    dist = setuptools.dist.Distribution({
        "name": distribution.key,
        "version": distribution.version,
        "entry_points": {
            "console_scripts": [
                str(val) for _, val in
                entry_points
            ]
        }
    })
    dist.script_name = "setup.py"
    # TODO(Jonathon): This should be imported up top as:
    # import setuptools.command.install_scripts.install_scripts
    # but don't know why it doesn't work right now.
    from setuptools.command.install_scripts import install_scripts
    cmd = install_scripts(dist)
    cmd.install_dir = bin_dir_path
    cmd.ensure_finalized()
    cmd.run()

    # Bazel py_binary rule requires files to have '.py' suffix.
    # Example errors:
    # "source file '//thing:foobar' is misplaced here (expected .py)"
    # "//thing:main does not produce any py_binary srcs files (expected .py)"
    for f in os.listdir(bin_dir_path):
        if not f.endswith(".py"):
            fullname = os.path.join(bin_dir_path, f)
            os.rename(fullname, fullname + ".py")

    return [key for key, _ in entry_points]


def entry_point_build_file_targets(pkg_name, entry_point_names) -> str:
    return "\n".join(
        _bazel_py_target_for_entry_point(pkg_name, entry_point_name)
        for entry_point_name
        in entry_point_names
    )


def _bazel_py_target_for_entry_point(pkg_name, entry_point_name):
    template = """\
py_binary(
    name = "{rule}",
    srcs = ["bin/{entry_point}.py"],
    deps = [":{pkg_name}"],
    visibility = ["//visibility:public"],
    main = "bin/{entry_point}.py",
)
"""
    return template.format(
        # Avoid clashing modules on import path.
        # Ref: https://github.com/bazelbuild/bazel/issues/7091#issuecomment-492947750
        rule=f"bin-{entry_point_name}" if entry_point_name == pkg_name else entry_point_name,
        entry_point=entry_point_name,
        pkg_name=pkg_name,
    )
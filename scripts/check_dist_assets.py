"""Verify every non-Python package file ships in the built distributions.

The inspector and static browser load files from the package directory
at runtime (static/, snapshot.js via Path(__file__).parent), so a wheel
missing them installs fine and breaks later. Expected files are
derived from the source tree, so new assets are covered automatically.

Used by the CI `build` job and the release workflow. Run after
`uv build`:  uv run --no-project python scripts/check_dist_assets.py
"""

import glob
import sys
import tarfile
import zipfile
from pathlib import Path


def main() -> int:
    pkg = Path("src/account_vault")
    expected = sorted(
        str(p.relative_to(pkg))
        for p in pkg.rglob("*")
        if p.is_file() and p.suffix not in {".py", ".pyc"} and "__pycache__" not in p.parts
    )
    if not expected:
        print("FAILED: no package assets found in source tree", file=sys.stderr)
        return 1
    wheels = glob.glob("dist/*.whl")
    sdists = glob.glob("dist/*.tar.gz")
    if not wheels or not sdists:
        print(f"FAILED: dist/ must contain a wheel and an sdist (found {wheels + sdists})", file=sys.stderr)
        return 1
    wheel_names = set(zipfile.ZipFile(wheels[0]).namelist())
    missing = [rel for rel in expected if f"account_vault/{rel}" not in wheel_names]
    sdist_names = set(tarfile.open(sdists[0]).getnames())
    missing += [rel for rel in expected if not any(n.endswith(f"src/account_vault/{rel}") for n in sdist_names)]
    if missing:
        print(f"FAILED: assets missing from distributions: {missing}", file=sys.stderr)
        return 1
    print(f"OK: all {len(expected)} assets in wheel and sdist: {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

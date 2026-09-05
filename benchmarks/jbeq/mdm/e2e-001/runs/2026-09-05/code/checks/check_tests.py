"""U2 の受け入れ検査: 移行モジュールのテストが存在し、通ること。"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guard

_guard.require("tests/test_mdm_transform.py")


def main():
    proc = subprocess.run(
        [sys.executable, os.path.join(_guard.ROOT, "tests",
                                      "test_mdm_transform.py")],
        cwd=_guard.ROOT, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        print(output)
        _guard.fail("tests/test_mdm_transform.py が失敗した")
    if "Ran 0 tests" in output:
        _guard.fail("テストが 0 件しか実行されていない")
    last = [line for line in output.splitlines() if line.strip()]
    print("check_tests PASS: %s" % (last[-1] if last else "OK"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

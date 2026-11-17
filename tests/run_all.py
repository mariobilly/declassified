"""Runs every test (engine, server end-to-end, UI) and writes the review folder:
tests\\_out\\report.html (open it to see everything; DECLASSIFIED_TESTS picks another folder)."""
import json
import os
import shutil
import sys
import time
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C  # noqa: E402


class Result(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rows = []
        self._t = {}

    def startTest(self, test):
        self._t[test.id()] = time.perf_counter()
        super().startTest(test)

    def _row(self, test, status, err=None):
        tid = test.id()
        doc = (test.shortDescription() or "")
        self.rows.append({"id": tid, "suite": tid.split(".")[0], "name": tid.split(".")[-1], "status": status,
                          "seconds": round(time.perf_counter() - self._t.get(tid, time.perf_counter()), 2),
                          "doc": doc, "error": self._exc_info_to_string(err, test)[-1500:] if err else None})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._row(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._row(test, "FAIL", err)

    def addError(self, test, err):
        super().addError(test, err)
        self._row(test, "ERROR", err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._row(test, "skip")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._row(subtest, "FAIL", err)


def main():
    warnings.simplefilter("ignore")
    fresh = "--keep" not in sys.argv
    if fresh:   # outputs are rebuilt every run; the inputs stay
        for key, sub in C.DIRS.items():
            if key != "inputs":
                shutil.rmtree(os.path.join(C.TEST_DIR, sub), ignore_errors=True)
        shutil.rmtree(os.path.join(C.TEST_DIR, "_data"), ignore_errors=True)
    os.makedirs(os.path.join(C.TEST_DIR, "_data"), exist_ok=True)
    C.prepare_inputs()
    t0 = time.time()
    rows = []
    for mod in ("test_engine", "test_server", "test_ui"):
        suite = unittest.defaultTestLoader.loadTestsFromName(mod)
        runner = unittest.TextTestRunner(verbosity=2, resultclass=Result)
        res = runner.run(suite)
        rows += res.rows
    summary = {"when": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t0),
               "passed": sum(r["status"] == "pass" for r in rows), "total": len(rows), "tests": rows}
    with open(os.path.join(C.TEST_DIR, "_data", "results.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    import make_report
    path = make_report.build()
    print(f"\n{summary['passed']}/{summary['total']} passed in {summary['seconds']} s -> {path}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())

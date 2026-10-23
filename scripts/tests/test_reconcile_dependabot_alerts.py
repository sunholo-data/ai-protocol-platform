import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "reconcile", Path(__file__).resolve().parents[1] / "reconcile_dependabot_alerts.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def alert(*ranges):
    package = {"ecosystem": "npm", "name": "example"}
    return {
        "dependency": {"package": package},
        "security_advisory": {
            "vulnerabilities": [
                {"package": package, "vulnerable_version_range": value}
                for value in ranges
            ]
        },
    }


def lock(*versions):
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            **{
                f"node_modules/parent{i}/node_modules/example": {"version": value}
                for i, value in enumerate(versions)
            },
        },
    }


class ReconcileTests(unittest.TestCase):
    def test_pagination_and_missing_inventory(self):
        self.assertEqual(
            module.decode_pages('[{"number":1}]\n[]\n[{"number":2}]'),
            [[{"number": 1}], [], [{"number": 2}]],
        )
        for raw in ["", '{"message":"Forbidden"}', "not JSON"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                module.decode_pages(raw)

    def test_checks_nested_copies_and_parallel_release_ranges(self):
        advisory = alert("< 1.13.6", ">= 1.14.0, < 1.14.5")
        self.assertFalse(module.fixed_in_lock(advisory, lock("1.13.6", "1.14.3")))
        self.assertTrue(module.fixed_in_lock(advisory, lock("1.13.6", "1.14.5")))

    def test_prerelease_and_unknown_ranges_never_count_as_fixed(self):
        for version, expression in [
            ("0.9.1-alpha.0", ">= 0.9.0, < 0.10.2"),
            ("1.2.3", "^1.2.0"),
        ]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                module.fixed_in_lock(alert(expression), lock(version))

    def test_removed_package_is_fixed_but_malformed_inventory_is_not(self):
        self.assertTrue(module.fixed_in_lock(alert("< 2.0.0"), lock()))
        with self.assertRaises(KeyError):
            module.fixed_in_lock(alert("< 2.0.0"), {})

    def test_range_boundaries(self):
        self.assertTrue(module.in_range("2.0.7", ">= 1.1.0, < 2.0.8"))
        self.assertFalse(module.in_range("2.0.8", ">= 1.1.0, < 2.0.8"))
        self.assertTrue(module.in_range("5.0.9", "< 1.1.21 || >= 4.0.0, < 5.0.12"))


if __name__ == "__main__":
    unittest.main()

"""Guard the application's persistence boundary as new features are added."""
import ast
import unittest
from pathlib import Path

import aws_study


class ArchitectureTests(unittest.TestCase):
    def test_sql_execution_stays_in_repositories_and_database_setup(self):
        package = Path(aws_study.__file__).parent
        violations = []
        for path in package.rglob("*.py"):
            if path.name == "db.py" or path.name.endswith("_repository.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {
                        "execute", "executemany", "executescript",
                    }
                ):
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [], "SQL leaked outside persistence")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "protocol" / "v2"
SCHEMAS = CONTRACT / "schemas"
EXAMPLES = CONTRACT / "examples"

JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Check a payload against the subset of JSON Schema the contract uses.

    The server has no third-party dependencies, and pulling one in to read
    nine small schemas would cost more than it returns. This covers type,
    required, properties, items and enum, which is everything the contract
    expresses.
    """
    errors: list[str] = []

    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        allowed = tuple(
            JSON_TYPES[name] if not isinstance(JSON_TYPES[name], tuple) else JSON_TYPES[name]
            for name in names
        )
        flat: list[type] = []
        for entry in allowed:
            flat.extend(entry if isinstance(entry, tuple) else (entry,))
        # A JSON boolean is a Python bool, which is also an int. Only accept it
        # where the schema actually asks for one.
        if isinstance(value, bool) and bool not in flat:
            errors.append(f"{path}: förväntade {names}, fick boolean")
            return errors
        if not isinstance(value, tuple(flat)):
            errors.append(f"{path}: förväntade {names}, fick {type(value).__name__}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} finns inte i {schema['enum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: obligatoriskt fält {key} saknas")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                errors.extend(validate(value[key], subschema, f"{path}.{key}"))

    if isinstance(value, list) and "items" in schema:
        for index, entry in enumerate(value):
            errors.extend(validate(entry, schema["items"], f"{path}[{index}]"))

    return errors


def message_name(example: Path) -> str:
    """fiktiv-config-ack.json -> config-ack"""
    return example.stem.split("-", 1)[1]


class ProtocolContractTests(unittest.TestCase):
    def test_every_example_matches_its_schema(self):
        examples = sorted(EXAMPLES.glob("*.json"))
        self.assertTrue(examples, "protokollkontraktet saknar exempel")
        for example in examples:
            with self.subTest(example=example.name):
                schema_path = SCHEMAS / f"{message_name(example)}.schema.json"
                self.assertTrue(
                    schema_path.is_file(),
                    f"{example.name} har inget schema {schema_path.name}",
                )
                errors = validate(
                    json.loads(example.read_text()),
                    json.loads(schema_path.read_text()),
                )
                self.assertEqual(errors, [], f"{example.name}: {errors}")

    def test_every_message_type_has_a_worked_example(self):
        documented = {path.name.removesuffix(".schema.json") for path in SCHEMAS.glob("*.json")}
        illustrated = {message_name(path) for path in EXAMPLES.glob("*.json")}
        self.assertEqual(
            documented - illustrated,
            set(),
            "meddelandetyper utan exempel",
        )

    def test_the_command_catalogue_documents_every_action(self):
        schema = json.loads((SCHEMAS / "command.schema.json").read_text())
        contract = (CONTRACT / "README.md").read_text()
        for action in schema["properties"]["action"]["enum"]:
            with self.subTest(action=action):
                self.assertIn(
                    f"`{action}`",
                    contract,
                    f"{action} finns i schemat men inte i kommandokatalogen",
                )

    def test_both_reference_configurations_are_published(self):
        # Decision B5: Charlottendal is the real integration reference and the
        # fictional topology is the constructed unit-test fixture. Losing
        # either one quietly narrows what the contract is proven against.
        references = {path.stem.split("-", 1)[0] for path in EXAMPLES.glob("*.json")}
        self.assertIn("charlottendal", references)
        self.assertIn("fiktiv", references)


if __name__ == "__main__":
    unittest.main()

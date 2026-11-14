"""Audit graph field names against downloaded upstream source AST without importing torch."""

import ast

from core.common import ROOT, read_json, write_json
from core.workflow_manager import validate


def contracts():
    result = {}
    files = [ROOT / "docs/upstream/comfy/nodes.py", ROOT / "docs/upstream/ipadapter/IPAdapterPlus.py"]
    files += list((ROOT / "docs/upstream/comfy/comfy_extras").glob("*.py"))
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            contract = {"required": [], "optional": [], "source": str(path.relative_to(ROOT)).replace("\\", "/")}
            for method in methods:
                if method.name == "INPUT_TYPES":
                    for statement in ast.walk(method):
                        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Dict):
                            for key, value in zip(statement.value.keys, statement.value.values):
                                if (
                                    isinstance(key, ast.Constant)
                                    and key.value in {"required", "optional"}
                                    and isinstance(value, ast.Dict)
                                ):
                                    contract[key.value] = [
                                        k.value
                                        for k in value.keys
                                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                                    ]
                elif method.name == "define_schema":
                    for statement in ast.walk(method):
                        if (
                            not isinstance(statement, ast.Call)
                            or not isinstance(statement.func, ast.Attribute)
                            or statement.func.attr != "Input"
                        ):
                            continue
                        if (
                            statement.args
                            and isinstance(statement.args[0], ast.Constant)
                            and isinstance(statement.args[0].value, str)
                        ):
                            optional = any(
                                k.arg == "optional" and isinstance(k.value, ast.Constant) and k.value.value is True
                                for k in statement.keywords
                            )
                            contract["optional" if optional else "required"].append(statement.args[0].value)
            if contract["required"] or contract["optional"]:
                result[cls.name] = contract
    return result


def audit(graph, definitions=None):
    definitions = definitions or contracts()
    validate(graph)
    for item in graph.values():
        kind, inputs = item["class_type"], set(item["inputs"])
        definition = definitions[kind]
        if kind == "SaveVideo":
            # _save_video_codec_input is a top-level helper building nested V3 DynamicCombo inputs.
            # See vendored nodes_video.py and execution_test/dynamic_inputs_test.py for dot-key serialization.
            source = (ROOT / definition["source"]).read_text(encoding="utf-8")
            assert '"codec"' in source and '"encoding"' in source and '"crf"' in source
            definition = {
                "required": ["video", "filename_prefix", "format", "format.codec"],
                "optional": ["format.codec.encoding", "format.codec.encoding.crf"],
            }
        missing = set(definition["required"]) - inputs
        extra = inputs - set(definition["required"]) - set(definition["optional"])
        if missing or extra:
            raise ValueError(f"Upstream contract mismatch {kind}: missing={missing}, extra={extra}")


def main():
    definitions = contracts()
    checked = []
    for path in (ROOT / "workflows/api").glob("*.json"):
        audit(read_json(path), definitions)
        checked.append(str(path.relative_to(ROOT)))
    write_json(
        ROOT / "reports/upstream-contracts.json",
        {
            "checked": checked,
            "contracts": definitions,
            "validation": "static source AST; live /object_info validation still required",
        },
    )
    print("Upstream source contracts passed:", ", ".join(checked))


if __name__ == "__main__":
    main()

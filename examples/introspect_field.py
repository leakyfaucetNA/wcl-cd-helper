"""Print the GraphQL introspection for a given type's field — full
docstrings on the field and on every argument. Useful for resolving
ambiguity in WCL's published docs.

Usage:
    python -m examples.introspect_field <TypeName> <fieldName>

Example:
    python -m examples.introspect_field Encounter fightRankings
"""
from __future__ import annotations

import asyncio
import json
import sys

from dotenv import load_dotenv

from wcl_bot.wcl import WCLClient

INTROSPECT = """
query Introspect($name: String!) {
  __type(name: $name) {
    name
    description
    fields {
      name
      description
      type { name kind ofType { name kind } }
      args {
        name
        description
        defaultValue
        type {
          name
          kind
          ofType { name kind ofType { name kind } }
        }
      }
    }
  }
}
"""


def _type_name(t: dict) -> str:
    if not t:
        return "?"
    if t.get("name"):
        return t["name"]
    inner = t.get("ofType") or {}
    return f"[{_type_name(inner)}]" if t.get("kind") == "LIST" else _type_name(inner)


async def main(type_name: str, field_name: str) -> int:
    async with WCLClient() as client:
        data = await client.execute(INTROSPECT, {"name": type_name})
    t = data["__type"]
    if not t:
        print(f"No such type: {type_name!r}")
        return 1
    field = next((f for f in t["fields"] if f["name"] == field_name), None)
    if not field:
        print(f"{type_name} has no field {field_name!r}. Available fields:")
        for f in t["fields"]:
            print(f"  - {f['name']}")
        return 1

    print(f"=== {type_name}.{field_name} : {_type_name(field['type'])}")
    print(f"\n{field.get('description') or '(no description)'}\n")
    if not field["args"]:
        print("(no arguments)")
        return 0
    print("Arguments:")
    for arg in field["args"]:
        type_str = _type_name(arg["type"])
        default = f" = {arg['defaultValue']}" if arg.get("defaultValue") else ""
        print(f"\n  {arg['name']}: {type_str}{default}")
        if arg.get("description"):
            for line in arg["description"].splitlines():
                print(f"      {line}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    load_dotenv()
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))

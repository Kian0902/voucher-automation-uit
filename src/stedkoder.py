

import os
import json
from pathlib import Path



def _flatten(units, parent, default_type, result) -> None:
    """
    Walk one level of the nested JSON tree and fill the flat dict.

    Args:
        units (dict): {code: unit info} at the current tree level.
        parent (str | None): Code of the unit these entries nest under.
        default_type (str): Type for entries that do not state their own.
        result (dict): The flat dict being filled, {code: entry}.
    """


    for code, info in units.items():
        entry = {
            "short_name": info["short_name"],
            "name": info["name"],
            "parent": parent,
            "type": info.get("type", default_type)}


        # Optional fields are only present when the registry has them (like "campus")
        for optional_field in ("campus", "note", "aliases"):
            if optional_field in info:
                entry[optional_field] =info[optional_field]
        result[code] = entry

        emptD = {}
        children = info.get("children", emptD)
        _flatten(children, code, default_type, result)


# Loadingf the registry
_json_path = Path(__file__).resolve().parent.parent / "data" / "stedkoder.json"
with open(_json_path, encoding="utf-8") as json_file:
    _registry = json.load(json_file)

STEDKODER = {}
for _group in ("avdeling", "fakultet", "toppenhet"):
    _flatten(_registry[_group], None, _group, STEDKODER)
RETIRED = _registry["retired"]
UPDATED = _registry["updated"]


if __name__ == "__main__":
    # Testing/Checking quality. Every child code starts with correspoding parent code, types are known, retired codes point to real units
    for code, info in STEDKODER.items():
        parent = info["parent"]
        if parent is not None:
            assert code.startswith(parent), f"{code}: not a child of {parent}"
        assert info["type"] in ("fakultet", "toppenhet", "avdeling", "seksjon", "institutt", "senter", "underenhet"), \
            f"{code}: unknown type {info['type']}"

    for old_code, new_code in RETIRED.items():
        assert new_code in STEDKODER, f"retired {old_code}: unknown successor {new_code}"

    assert len(STEDKODER) == 97, f"expected 97 units, got {len(STEDKODER)}"
    assert STEDKODER["3214"]["short_name"] == "IFI"
    assert STEDKODER["3214"]["type"] == "institutt"
    assert STEDKODER["31"]["type"] == "fakultet"
    assert STEDKODER["33192100"]["parent"] == "3319"
    assert STEDKODER["3118"]["campus"] == "Alta"

    print(f"{len(STEDKODER)} stedkoder, {len(RETIRED)} retired,updated {UPDATED} - self-check passed")



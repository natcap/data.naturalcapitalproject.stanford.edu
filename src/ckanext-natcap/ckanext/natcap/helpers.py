import os


def natcap_hello():
    return "Hello, natcap!"


def _is_yaml_name(name: str) -> bool:
    n = (name or "").lower()
    return n.endswith(".yml") or n.endswith(".yaml")


def _strip_yaml_suffix(name: str) -> str:
    # returns 'foo.tif' for 'foo.tif.yml'
    if name.lower().endswith(".yaml"):
        return name[:-5]
    if name.lower().endswith(".yml"):
        return name[:-4]
    return name


def natcap_find_attached_metadata_map(pkg_dict: dict) -> dict:
    """
    Return a mapping {data_resource_id: metadata_resource_dict}
    using filename convention <data_filename>.yml (or .yaml).

    Rules:
      - Match by exact filename + '.yml/.yaml' in the same package.
      - Shapefiles: only the `.shp` gets metadata (`vector.shp.yml`).
      - We ignore standalone YAMLs *without* a corresponding data file.
    """
    resources = pkg_dict.get("resources", []) or []
    if not resources:
        return {}

    # Build lookup by name for YAMLs
    yaml_by_name = {}
    for r in resources:
        n = r.get("name") or os.path.basename(r.get("url", "")) or ""
        if _is_yaml_name(n):
            yaml_by_name[n] = r

    attached = {}
    for r in resources:
        # Determine the data filename we will match against
        name = r.get("name") or os.path.basename(r.get("url", "")) or ""
        low = name.lower()

        # Only attach metadata to the main file for shapefiles
        if low.endswith(".shx") or low.endswith(".dbf") or low.endswith(".prj") or low.endswith(".cpg"):
            continue

        # Try both extensions
        candidates = [f"{name}.yml", f"{name}.yaml"]
        for cand in candidates:
            meta = yaml_by_name.get(cand)
            if meta:
                attached[r["id"]] = meta
                break

    print(f"returning attached {attached}")
    return attached


def get_helpers():
    return {
        "natcap_hello": natcap_hello,
        "natcap_find_attached_metadata_map": natcap_find_attached_metadata_map,
    }

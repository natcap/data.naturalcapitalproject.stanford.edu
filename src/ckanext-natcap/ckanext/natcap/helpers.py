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


def natcap_find_source_metadata_map(pkg, sources):
    """
    Build a map of source_name -> metadata_source for all sources.
    Looks for YAML files that match data file names (e.g., data.tif -> data.tif.yml)

    Returns dict: {source_name: metadata_source_dict}
    """
    meta_map = {}

    def build_yaml_lookup(sources_list):
        """Build a dict of yaml_filename -> yaml_source"""
        yaml_lookup = {}
        for source in sources_list:
            name = source.get('name', '').lower()
            # Check if it's a YAML file
            if name.endswith('.yml') or name.endswith('.yaml'):
                yaml_lookup[name] = source
            # Recursively process children
            if source.get('children'):
                yaml_lookup.update(build_yaml_lookup(source['children']))
        return yaml_lookup
    
    def process_sources_recursive(sources_list, yaml_lookup):
        """Match data files to their YAML metadata"""
        for source in sources_list:
            name = source.get('name', '')
            name_lower = name.lower()

            # Skip if this IS a YAML file
            if name_lower.endswith('.yml') or name_lower.endswith('.yaml'):
                continue

            # Skip shapefile auxiliary files
            if (name_lower.endswith('.shx') or
                name_lower.endswith('.dbf') or
                name_lower.endswith('.prj') or
                name_lower.endswith('.cpg')):
                continue

            # Try to find matching YAML (try both .yml and .yaml)
            candidates = [
                f"{name_lower}.yml".lower(),
                f"{name_lower}.yaml".lower(),
                f"{name_lower}-metadata.yml".lower(),
                f"{name_lower}-metadata.yaml".lower()
            ]

            for candidate in candidates:
                yaml_source = yaml_lookup.get(candidate)
                if yaml_source:
                    # Use name as key instead of id
                    meta_map[name] = yaml_source
                    break

            # Recursively process children
            if source.get('children'):
                process_sources_recursive(source['children'], yaml_lookup)

    # First pass: build lookup of all YAML files
    yaml_lookup = build_yaml_lookup(sources)

    # Second pass: match data files to YAMLs
    process_sources_recursive(sources, yaml_lookup)

    return meta_map


def get_helpers():
    return {
        "natcap_hello": natcap_hello,
        "natcap_find_attached_metadata_map": natcap_find_attached_metadata_map,
        "natcap_find_source_metadata_map": natcap_find_source_metadata_map,
    }

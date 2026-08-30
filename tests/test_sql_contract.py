from pathlib import Path


SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "ecociente_schema.sql"
ANALYTICS_DIR = Path(__file__).resolve().parents[1] / "src" / "agents" / "analytics" / "tools"


def test_schema_file_is_present_and_contains_core_objects() -> None:
    text = SCHEMA.read_text(encoding="utf-8").lower()
    for object_name in (
        "tb_postagens",
        "tb_lkp_categorias_residuos",
        "tb_lkp_status_validacoes_postagens",
        "tb_torres",
        "tb_moradores",
        "tb_rel_usuarios_condominios",
        "tb_quizzes",
        "tb_tentativas_quiz",
    ):
        assert object_name in text


def test_analytics_does_not_reference_removed_schema_objects() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in ANALYTICS_DIR.glob("*.py"))
    removed_names = [
        "tb_" + "unidades",
        "fn_" + "ritmo_diario_torres",
        "fn_" + "projecao_reciclagem",
    ]
    assert all(name not in source for name in removed_names)


def test_dotenv_is_loaded_only_in_core_config() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    matches = []
    for path in root.rglob("*.py"):
        if "load_dotenv" in path.read_text(encoding="utf-8"):
            matches.append(path.relative_to(root).as_posix())
    assert matches == ["core/config.py"]


def _schema_columns() -> dict[str, set[str]]:
    import re

    text = SCHEMA.read_text(encoding="utf-8")
    result: dict[str, set[str]] = {}
    pattern = re.compile(r"CREATE TABLE\s+(tb_[a-z0-9_]+)\s*\((.*?)\n\);", re.I | re.S)
    for match in pattern.finditer(text):
        table = match.group(1).lower()
        columns: set[str] = set()
        for line in match.group(2).splitlines():
            stripped = line.strip()
            column = re.match(r"([a-z_][a-z0-9_]*)\s+", stripped, re.I)
            if column and column.group(1).upper() not in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
                columns.add(column.group(1).lower())
        result[table] = columns
    return result


def test_analytics_qualified_columns_exist_in_delivered_schema() -> None:
    import re

    columns = _schema_columns()
    problems: list[tuple[str, str, str]] = []
    for path in ANALYTICS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        aliases: dict[str, set[str]] = {}
        for table, alias in re.findall(r"\b(?:FROM|JOIN)\s+(tb_[a-z0-9_]+)\s+([a-z][a-z0-9_]*)", source, re.I):
            aliases.setdefault(alias.lower(), set()).add(table.lower())
        unambiguous = {alias: next(iter(tables)) for alias, tables in aliases.items() if len(tables) == 1}
        for alias, column in re.findall(r"\b([a-z][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", source, re.I):
            table = unambiguous.get(alias.lower())
            if table and table in columns and column.lower() not in columns[table]:
                problems.append((path.name, table, column.lower()))
    assert problems == []


def test_env_example_matches_canonical_settings_variables() -> None:
    import re

    root = Path(__file__).resolve().parents[1]
    config = (root / "src" / "core" / "config.py").read_text(encoding="utf-8")
    used = set(re.findall(r'_(?:get|bool|int|float)\("([A-Z0-9_]+)"', config))
    declared = {
        line.split("=", 1)[0].strip()
        for line in (root / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    assert declared == used

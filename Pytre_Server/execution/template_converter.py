import re

_PARAM_PATTERN = re.compile(r"%\((@[\d\w#_$@]+)\)s")


def to_positional_style(cmd_template: str, cmd_parameters: dict) -> tuple[str, list]:
    """Convert a template using %(name)s style and a dict for parameters into a
    template using @P1, @P2... style and an ordered list of parameters."""

    name_pos: dict[str, int] = {}

    def replace_func(match: re.Match) -> str:
        name = match.group(1)
        if name not in name_pos:
            name_pos[name] = len(name_pos) + 1
        return f"@P{name_pos[name]}"

    template = _PARAM_PATTERN.sub(replace_func, cmd_template)
    parameters = [cmd_parameters[name] for name in name_pos.keys()]

    return template, parameters

import re
from collections.abc import Callable
from datetime import datetime
from dateutil.relativedelta import relativedelta

from Pytre import settings, users
from Pytre.sql_query.converter import SqlTypeConverter

# Patterns for parsing SQL parameters when SqlParam is initializated
VAR_NAME_PATTERN = re.compile(r"^@!?[\d\w#_\$@]+")
TYPE_NAME_PATTERN = re.compile(r"^[^(=]+as\s+([^(\s=]+)", re.IGNORECASE)
DISPLAY_VALUE_PATTERN = re.compile(r"^[^=]*=\s*('.*?'|[^,\s]*)")
TYPE_ARGS_PATTERN = re.compile(r"^[^(=]+\(([^)]+)\)")
COMMENT_PATTERN = re.compile(r"--\s*(.+)")
INFO_LIST_PATTERN = re.compile(r"^(.*?)(?=\(|$)\(?(.*?)\)?$")

DECLARE_EXTRACT_PATTERN = re.compile(r"^(DECLARE[^;]*;)", re.MULTILINE | re.DOTALL)  # for SqlParameters


class SqlParam:
    def __init__(self, settings: settings.Settings, user: users.CurrentUser, sql_declare: str):
        self.sql_declare = sql_declare

        app_settings: settings.Settings = settings
        self.converter = SqlTypeConverter(
            date_display=app_settings.date_format,
            decimal_separator=app_settings.decimal_separator,
        )  # object to convert string to value or value to string

        self.user: users.CurrentUser = user

        self.var_name: str = ""
        self.type_name: str = ""
        self.type_args: list = []

        self.description: str = ""
        self.is_optional: bool = False
        self.is_hidden: bool = False
        self.ui_control: str = ""
        self.authorized_values: dict = {}
        self.ctrl_pattern: re.Pattern | None = None
        self.is_inlined: bool = False

        self.display_value: str = ""
        self.value_cmd: str | int | float = ""

        self.value_is_ok: bool = False
        self.ctrl_pattern_is_ok: bool = False

        self.func_dict: dict[str, Callable[..., str]] = self._funcs_init()

        self.set_default()

    # ------------------------------------------------------------------------------------------
    # Default value initialization
    # ------------------------------------------------------------------------------------------
    def set_default(self) -> None:
        match = VAR_NAME_PATTERN.search(self.sql_declare)
        self.var_name = match.group(0) if match else ""

        match = TYPE_NAME_PATTERN.search(self.sql_declare)
        self.type_name = match.group(1).lower() if match else ""
        self.type_args = self._get_type_args()

        match = DISPLAY_VALUE_PATTERN.search(self.sql_declare)
        self.display_value = match.group(1).replace("'", "").strip() if match else ""

        self._infos_from_comment()

        # Convert default display value to default format and validate with converter
        self.display_value = self.authorized_values.get(self.display_value, self.display_value)
        self.display_value = self.converter.to_display(self.type_name, self.display_value)

        # Update value_cmd to ensure value_is_ok is properly initialized
        self.update_value_cmd()

    def _get_type_args(self) -> list:
        str_type_args = TYPE_ARGS_PATTERN.search(self.sql_declare)
        str_type_args = str_type_args[1].lower().split(",") if str_type_args else []
        return [arg.strip() for arg in str_type_args]

    def _infos_from_comment(self) -> None:
        comment = COMMENT_PATTERN.search(self.sql_declare)
        if not comment:
            return

        comment_infos = comment[1].split("|")

        # description
        if comment_infos[0]:
            self.description = comment_infos[0]

        # optional information or UI control
        if len(comment_infos) > 1 and comment_infos[1]:
            infos = re.sub(r"(\(.*?\))|(,)", r"\1^^^", comment_infos[1])
            infos = [m.strip() for m in re.split(r"\^{3,6}", infos) if m.strip()]

            ui_controls = ("entry", "list", "check")
            calc_funcs = ("user_info", "fiscal_year", "month_end", "today")

            for info in infos:
                info_lst = INFO_LIST_PATTERN.search(info)
                if info_lst is None:
                    continue

                info_func = info_lst[1].lower()
                info_args = info_lst[2]

                if info_func == "optional":
                    self.is_optional = True
                elif info_func == "hide":
                    self.is_hidden = True
                elif info_func in calc_funcs:  # self.func_dict:
                    self.display_value = self._calc_func(info_func, info_args)
                elif info_func in ui_controls or info_func == "":
                    self.ui_control = info_func
                    self._authorized_values(info_func, info_args)

        # should parameter be hardcoded and not parameterized
        if self.var_name.startswith("@!"):
            allowed_for_inlined = ("list", "check")
            if self.ui_control not in allowed_for_inlined:
                raise ValueError(
                    f"{self.var_name} is not parameterized. " + f"Only {allowed_for_inlined} can be used as control"
                )

            self.is_inlined = True

        # ctrl pattern regex for display value validation
        if len(comment_infos) > 2 and comment_infos[2]:
            self.ctrl_pattern = re.compile(("|").join(comment_infos[2:]))

    def _authorized_values(self, ctrl: str, args: str) -> None:
        my_args = args.split(",")

        for i, arg in enumerate(my_args):
            key_val = arg.split(":")
            key = key_val[0].strip()

            if ctrl != "check":
                val = key_val[1].strip() if len(key_val) > 1 else key
            else:
                val = "on" if i == 0 else "off"

            self.authorized_values[key] = val

    # ------------------------------------------------------------------------------------------
    # Dynamic default value functions
    # ------------------------------------------------------------------------------------------
    def _funcs_init(self) -> dict[str, Callable[..., str]]:
        def user_info(attr: str) -> str:
            if attr in self.user.users.attribs_std:
                info = getattr(self.user, attr, "")
            else:
                info = self.user.attribs_cust.get(attr, "")

            return info

        def fiscal_year(
            last_month: int, month_offset: int = 0, days_offset: int = 0, today_mth_offset: int = 0
        ) -> str:
            last_month = int(last_month)
            month_offset = int(month_offset)
            days_offset = int(days_offset)
            today_mth_offset = int(today_mth_offset)

            date_ref = datetime.today() + relativedelta(months=today_mth_offset)
            my_year = date_ref.year
            my_year += 1 if date_ref.month > last_month else 0

            my_date = (
                datetime(my_year, last_month, 1)
                + relativedelta(months=month_offset + 1)
                + relativedelta(days=-1 + days_offset)
            )

            return datetime.strftime(my_date, self.converter.date_cmd_format)

        def month_end(month_offset: int = 0, days_offset: int = 0) -> str:
            month_offset = int(month_offset)
            days_offset = int(days_offset)

            my_date = datetime(datetime.today().year, datetime.today().month, 1)
            my_date += relativedelta(months=month_offset + 1) + relativedelta(days=-1 + days_offset)

            return datetime.strftime(my_date, self.converter.date_cmd_format)

        def today(days_offset: int = 0) -> str:
            days_offset = int(days_offset)

            my_date = datetime.today()
            my_date += relativedelta(days=days_offset)

            return datetime.strftime(my_date, self.converter.date_cmd_format)

        return {"user_info": user_info, "fiscal_year": fiscal_year, "month_end": month_end, "today": today}

    def _calc_func(self, func: str, func_args: str) -> str:
        my_args = func_args.lower().split(",") if func_args else []
        my_func = self.func_dict.get(func)

        if callable(my_func):
            return my_func(*my_args)

        return self.display_value

    # ------------------------------------------------------------------------------------------
    # Display value conversion and validation
    # ------------------------------------------------------------------------------------------
    def update_value_cmd(self) -> str | int | float:
        """
        Updates value_cmd from display_value.

        Validates that value is within authorized values and provided if mandatory (blocking).
        Tests ctrl_pattern match (non-blocking, for UI warning only).

        Returns:
            Converted value for SQL command
        Raises:
            ValueError: if validation fails or ctrl_pattern regex is invalid
        """

        self.value_cmd = ""
        self.ctrl_pattern_is_ok = False
        self.value_is_ok = False

        self.value_cmd = self.convert_display_value_to_cmd(self.display_value)  # ValueError if validation fails
        self.ctrl_pattern_is_ok = self.test_ctrl_pattern(self.display_value)  # ValueError if ctrl_pattern invalid

        # update display_value for date to default format
        if self.type_name in ("date", "datetime") and self.display_value != "":
            self.display_value = self.converter.to_display(self.type_name, self.value_cmd)

        self.value_is_ok = True

        return self.value_cmd

    def convert_display_value_to_cmd(self, val_to_convert: str) -> str | int:
        """ValueError: if value is not valid, or missing and not optional"""

        if self.authorized_values:
            if val_to_convert not in self.authorized_values.values():
                raise ValueError(
                    f"{val_to_convert}, ne fait pas partie des valeurs autorisées : "
                    + ", ".join(self.authorized_values.values())
                )
            else:
                val_to_convert = [k for k, v in self.authorized_values.items() if v == val_to_convert][0]

        if not val_to_convert and not self.is_optional:
            raise ValueError(f"paramètre obligatoire pour {self.var_name}")

        return self.converter.to_cmd(self.type_name, val_to_convert, self.type_args)

    def test_ctrl_pattern(self, val_to_test: str) -> bool:
        """ValueError: if ctrl_pattern is not a valid one"""

        if not val_to_test or not self.ctrl_pattern:
            return True

        try:
            if self.ctrl_pattern.match(val_to_test):
                return True
        except re.error as e:
            raise ValueError(f"Regex invalide pour {self.var_name} : {self.ctrl_pattern.pattern}") from e

        return False


class SqlParameters:
    def __init__(self, settings: settings.Settings, user: users.CurrentUser, file_content: str):
        self.settings = settings
        self.user = user
        self.params: dict[str, SqlParam] = self._parse_declare(file_content)

    def _parse_declare(self, file_content: str) -> dict[str, SqlParam]:
        regex_match = DECLARE_EXTRACT_PATTERN.search(file_content)  # section DECLARE

        params_dict = {}
        params_txt = regex_match.group(1) if regex_match is not None else ""
        for line in params_txt.splitlines():
            if line[0:1] == "@":
                param = SqlParam(self.settings, self.user, line)
                params_dict[param.var_name] = param

        return params_dict

    def update_values(self, key: str | None = None) -> bool:
        """
        If key is none then update all key
        """
        if key is not None and key not in self.params:
            return False

        my_list = [key] if key is not None else self.params.keys()

        for key in my_list:
            self.params[key].update_value_cmd()

        return True

    def validate(self, key: str | None = None) -> bool:
        """
        If key is none then update all key
        """
        if key is not None and key not in self.params:
            return False

        my_list = [key] if key is not None else self.params.keys()

        for key in my_list:
            if not self.params[key].value_is_ok:
                return False

        return True

    def reset_values(self):
        for _, param in self.params.items():
            param.set_default()

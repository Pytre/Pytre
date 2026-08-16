from datetime import datetime, date


class SqlTypeConverter:
    def __init__(self, date_display: str = "%d/%m/%Y", decimal_separator: str = ","):
        self.date_display_format: str = date_display  # pour affichage utilisateurs ou extraction
        self.date_cmd_format: str = "%Y-%m-%d"  # pour commande SQL

        self.datetime_display_format: str = f"{date_display} %H:%M:%S"  # pour affichage utilisateurs ou extraction
        self.datetime_cmd_format: str = "%Y-%m-%d %H:%M:%S"  # pour commande SQL

        self.decimal_separator: str = decimal_separator

        self._to_cmd = _ToCmd(self)
        self._to_display = _ToDisplay(self)
        self._from_result = _FromResult(self)

    def to_cmd(self, type_name: str, string_to_convert: str, type_args=None) -> str | int:
        return self._to_cmd.transform(type_name, string_to_convert, type_args)

    def to_display(self, type_name: str, value_to_convert: str | int) -> str:
        return self._to_display.transform(type_name, value_to_convert)

    def from_result(self, value) -> str:
        return self._from_result.transform(value)


class _ToCmd:
    def __init__(self, parent):
        self.parent: SqlTypeConverter = parent
        self.func_dict: dict = self._func_dict()

    def transform(self, type_name: str, string_to_convert: str, type_args=None) -> str | int:
        if string_to_convert == "":
            return self._convert_to_null_value(type_name)

        if self.func_dict.get(type_name):
            if type_args:
                return self.func_dict[type_name](string_to_convert, type_args)
            else:
                return self.func_dict[type_name](string_to_convert)

        return string_to_convert

    def _func_dict(self) -> dict:
        return {
            "bit": self._str_to_bit,
            "int": self._str_to_int,
            "date": self._str_to_date,
            "datetime": self._str_to_datetime,
            "nchar": self._str_tochar,
            "char": self._str_tochar,
            "varchar": self._str_to_nvarchar,
            "nvarchar": self._str_to_nvarchar,
            "text": self._str_to_nvarchar,
            "ntext": self._str_to_nvarchar,
        }

    def _str_to_bit(self, string_to_convert: str) -> int:
        try:
            value = int(string_to_convert)
        except ValueError:
            raise ValueError(f"{string_to_convert} n'est pas un nombre entier valide")

        if value not in [0, 1]:
            raise ValueError(f"Erreur valeur, {string_to_convert} ne peut être que 0 ou 1")

        return value

    def _str_to_int(self, string_to_convert: str) -> int:
        try:
            value = int(string_to_convert)
        except ValueError:
            raise ValueError(f"{string_to_convert} n'est pas un nombre entier valide")

        return value

    def _str_to_date(self, string_to_convert: str) -> str:
        valid_formats: list[str] = ["%d%m%y", "%d%m%Y", "%d/%m/%y", "%d/%m/%Y"]

        # mettre en premier format spécifié par les settings
        if self.parent.date_display_format in valid_formats:
            valid_formats.remove(self.parent.date_display_format)
        valid_formats.insert(0, self.parent.date_display_format)

        for format_to_try in valid_formats:
            try:
                my_date = datetime.strptime(string_to_convert, format_to_try)
                return my_date.strftime(self.parent.date_cmd_format)
            except ValueError:
                continue

        raise ValueError(f"{string_to_convert} n'est pas une date valide (jj/mm/aaaa)")

    def _str_to_datetime(self, string_to_convert: str) -> str:
        valid_formats: list[str] = ["%d%m%y %H:%M:%S", "%d%m%Y %H:%M:%S", "%d/%m/%y %H:%M:%S", "%d/%m/%Y %H:%M:%S"]

        # mettre en premier format spécifié par les settings
        if self.parent.datetime_display_format in valid_formats:
            valid_formats.remove(self.parent.datetime_display_format)
        valid_formats.insert(0, self.parent.datetime_display_format)

        for format_to_try in valid_formats:
            try:
                my_date = datetime.strptime(string_to_convert, format_to_try)
                return my_date.strftime(self.parent.datetime_cmd_format)
            except ValueError:
                continue

        raise ValueError(f"{string_to_convert} n'est pas une date valide (jj/mm/aaaa hh:mm:ss)")

    def _str_to_nvarchar(self, string_to_convert: str, type_args: list[str] | None = None) -> str:
        max_size = 255 if not type_args or type_args[0] == "max" else int(type_args[0])

        if len(string_to_convert) > max_size:
            raise ValueError(f"{string_to_convert} a plus de caractères qu'autorisés (max : {max_size})")

        return string_to_convert

    def _str_tochar(self, string_to_convert: str, params: list[str] | None = None) -> str:
        size = int(params[0]) if params else 0

        if size == 0:
            raise ValueError("Taille non déclarée pour variable CHAR")

        if len(string_to_convert) != size:
            raise ValueError(f"{string_to_convert} n'est pas de la bonne taille ({size})")

        return string_to_convert

    def _convert_to_null_value(self, type_name: str) -> str | int:
        null_value_dict = {"int": 0}
        default_null_value = " "

        return null_value_dict.get(type_name, default_null_value)


class _ToDisplay:
    def __init__(self, parent):
        self.parent: SqlTypeConverter = parent
        self.func_dict: dict = self._func_dict()

    def transform(self, type_name: str, value_to_convert: str | int) -> str:
        if value_to_convert == "":
            return self._convert_to_null_value(type_name)

        if self.func_dict.get(type_name):
            return self.func_dict[type_name](value_to_convert)

        return str(value_to_convert)

    def _func_dict(self) -> dict:
        return {"date": self._date_to_str, "datetime": self._datetime_to_str}

    def _date_to_str(self, value_to_convert: str) -> str:
        return datetime.strptime(value_to_convert, self.parent.date_cmd_format).strftime(
            self.parent.date_display_format
        )

    def _datetime_to_str(self, value_to_convert: str) -> str:
        return datetime.strptime(value_to_convert, self.parent.datetime_cmd_format).strftime(
            self.parent.datetime_display_format
        )

    def _convert_to_null_value(self, type_name: str) -> str:
        null_value_dict = {"int": "0"}
        default_null_value = ""

        return null_value_dict.get(type_name, default_null_value)


class _FromResult:
    def __init__(self, parent):
        self.parent: SqlTypeConverter = parent
        self.func_dict: dict = self._func_dict()

    def transform(self, value_to_convert) -> str:
        type_name = str(type(value_to_convert))

        if self.func_dict.get(type_name):
            return self.func_dict[type_name](value_to_convert)
        else:
            return str(value_to_convert)

    def _func_dict(self) -> dict:
        return {
            "<class 'str'>": self._from_string,
            "<class 'decimal.Decimal'>": self._from_number,
            "<class 'float'>": self._from_number,
            "<class 'int'>": self._from_number,
            "<class 'bool'>": self._from_bool,
            "<class 'datetime.datetime'>": self._from_datetime,
            "<class 'datetime.date'>": self._from_date,
            "<class 'NoneType'>": self._from_none,
        }

    def _from_string(self, value: str) -> str:
        if value == " ":
            return ""

        return value

    def _from_number(self, value) -> str:
        str_value = str(value)

        if str_value.upper().startswith("0E-"):
            return ""

        # enleve trailing 0 des décimales
        if "." in str_value:
            str_value = str_value.rstrip("0").rstrip(".")

        # si séparateur décimal utiliser le séparateur décimal attendu
        if "." in str_value:
            str_value = str_value.replace(".", self.parent.decimal_separator)

        return str_value

    def _from_bool(self, value: bool) -> str:
        return "Vrai" if value else "Faux"

    def _from_datetime(self, value: datetime) -> str:
        if value == datetime(1753, 1, 1, 0, 0, 0):
            return ""

        date_format = self.parent.datetime_display_format
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            date_format = self.parent.date_display_format

        return value.strftime(date_format)

    def _from_date(self, value: date) -> str:
        if value == date(1753, 1, 1):
            return ""

        return value.strftime(self.parent.date_display_format)

    def _from_none(self, _) -> str:
        return ""
